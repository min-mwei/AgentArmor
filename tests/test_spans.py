"""Evidence spans from a source to the state, through the service, the client, the route and
the gate's record, and back to the source. Stand-ins only: a stub readout, no model.

What is checked is the path and its refusals. No trained evidence head exists; nothing here
says a span is right."""

import hashlib
import json
import threading
from http.server import HTTPServer
from types import SimpleNamespace

import numpy as np
import pytest
from agentarmor_monitor import decide, escalate, kai
from agentarmor_monitor.kai import DECISION, XPIA, KaiClient
from agentarmor_monitor.route import Routed
from agentarmor_vllm.encode import SPECIAL, tokenizer_sha256
from agentarmor_vllm.engine import Pointer, Service
from agentarmor_vllm.server import handler
from test_earlier_sessions import two_sessions

K0, K3, HEAD = "a" * 64, "b" * 64, "c" * 64


@pytest.fixture
def spool(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMMARIZER_HOME", str(tmp_path / "spool"))
    return tmp_path / "spool"


class Tokenizer:
    """One token for each character, and where each stands."""

    unk_token_id = -1
    backend_tokenizer = SimpleNamespace(to_str=lambda: "synthetic-tokenizer/v1")

    def convert_tokens_to_ids(self, text):
        return SPECIAL.index(text) + 1

    def __call__(self, text, **_):
        places = [(i, i + 1) for i in range(len(text))]
        return SimpleNamespace(input_ids=[ord(c) + 10 for c in text], offset_mapping=places)


class Backbone:
    name = "mlx-lora"

    def hidden(self, row):
        return np.asarray([[i % 7, 1] for i in range(len(row["ids"]))], dtype=np.float32)


class Stub:
    """A readout that marks what it is told to, and keeps what it was shown."""

    def __init__(self, plan=()):
        self.plan, self.seen = plan, []

    def spans(self, hidden, row, offsets, state):
        assert len(hidden) == len(row["ids"]) and offsets[0] == (0, 0)
        assert len(offsets) == len(state) + 1  # the marker and one token for each character
        self.seen.append(state)
        if isinstance(self.plan, Exception):
            raise self.plan
        return self.plan


def service(package, plan=None):
    tok = Tokenizer()
    meta = {"tokenizer_sha256": tokenizer_sha256(tok), "pauses": 0, "pause_id": None}
    meta |= {"max_state": 4096, "max_row": 6000, "max_packed": 9000, "temperature": 1.0}
    about = SimpleNamespace(meta=meta, identity=package, evidence={"readout": "stub"})
    pointer = Pointer({"q.weight": [[0, 0]], "q.bias": [2], "k.weight": [[1, 0]], "k.bias": [1]}, 2)
    made = Service(about, tok, pointer, Backbone())
    if plan is not None:
        made.evidence, made.head_sha256 = Stub(plan), HEAD
    return made


class Served:
    """Services answering on loopback, each with its own package, and a client for each."""

    def __init__(self, **plans):
        self.services = {
            K0: service(K0, plans.get("default")),
            K3: service(K3, plans.get("session")),
        }
        self.servers = [HTTPServer(("127.0.0.1", 0), handler(s)) for s in self.services.values()]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        urls = [f"http://127.0.0.1:{server.server_address[1]}" for server in self.servers]
        self.default = KaiClient(urls[0], expected=kai.profile(K0))
        self.session = KaiClient(urls[1], expected=kai.profile(K3))

    def routed(self):
        return Routed(self.default, self.session)

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()


@pytest.fixture
def served():
    made = []

    def make(**plans):
        made.append(Served(**plans))
        return made[-1]

    yield make
    for one in made:
        one.close()


def judged(identity, model, monkeypatch=None, maps=None):
    if maps is not None:  # the gate's own request, with its map kept for the test
        real = escalate.detect
        monkeypatch.setattr(decide, "detect", lambda *a, **k: real(*a, maps=maps, **k))
    return decide.judge(
        "claude_code", "s-late", "call-1", identity, model, "low", 12000, with_earlier=True
    )


def test_a_source_is_located_marked_served_admitted_and_resolved_to_itself(
    spool, served, monkeypatch
):
    identity, maps = two_sessions(), {}
    quiet = served(default=[], session=[])
    verdict, record, _ = judged(identity, quiet.routed(), monkeypatch, maps)
    assert (
        record["spans"][DECISION]["status"] == "empty" and "spans" not in record["spans"][DECISION]
    )
    (state,) = set(quiet.services[K3].evidence.seen)
    (found,) = [m for m in maps.values() if m is not None]
    # From the source: the request the earlier session was working on, its event and field.
    early = next(
        s for s in found.segments if s.get("session") == "s-early" and s.get("trust") == "user"
    )
    wanted = "Add a submit helper."
    (at,) = found.locate("s-early", early["seq"], "text", 4, len(wanted) - 1)
    assert (
        state[at[0] : at[1]] == wanted[4:-1]
        and found.missing("s-early", early["seq"], "text", 4, 9) is None
    )
    # Through the session checkpoint's head, the service, the client, the route and the gate.
    marking = served(default=[], session=[at])
    again, record, _ = judged(identity, marking.routed())
    assert again == verdict  # evidence changes no verdict
    told = record["spans"][DECISION]
    assert (
        told["status"] == "spans" and told["package_sha256"] == K3 and told["head_sha256"] == HEAD
    )
    (span,) = told["spans"]
    (piece,) = span["sources"]
    assert span["at"] == at and piece["source"] == [4, len(wanted) - 1]
    assert (piece["session"], piece["seq"], piece["field"], piece["trust"]) == (
        "s-early",
        early["seq"],
        "text",
        "user",
    )
    digest = hashlib.sha256(state.encode()).hexdigest()  # what the evidence is of, kept too
    assert record["spans"][XPIA] == {
        "status": "empty",
        "package_sha256": K0,
        "head_sha256": HEAD,
    } | {
        "state_sha256": digest,
        "readout": "stub",
    }
    assert told["state_sha256"] == digest and told["strict"]["rule"] == "exact"
    assert wanted not in json.dumps(record["spans"])  # identities and offsets, no text
    assert record["kai"]["evidence_available"] is True and record["policy_ready"] is False


def test_a_service_without_a_head_says_unavailable_and_the_record_keeps_that_apart(spool, served):
    verdict, record, _ = judged(two_sessions(), served().routed())
    assert {q: e["status"] for q, e in record["spans"].items()} == {
        DECISION: "unavailable",
        XPIA: "unavailable",
    }
    assert record["kai"]["evidence_available"] is False


@pytest.mark.parametrize(
    "plan",
    [
        [[5, 5]],
        [[9, 4]],
        [[0, 10**9]],
        [[4, 9], [8, 12]],
        [[1.0, 4]],
        [[True, 4]],
        [[1, 2, 3]],
        "x",
        RuntimeError("head"),
    ]
    + [[[i, i + 1] for i in range(0, 80, 2)]],
)
def test_a_head_that_fails_or_marks_what_is_not_an_interval_is_an_error_for_its_question(
    spool, served, plan
):
    identity = two_sessions()
    plain, _, _ = judged(identity, served(default=[], session=[]).routed())
    verdict, record, _ = judged(identity, served(default=[], session=plan).routed())
    assert record["spans"][DECISION] == {"status": "error", "why": "service", "package_sha256": K3}
    assert record["spans"][XPIA]["status"] == "empty"  # the other checkpoint's question stands
    assert verdict == plain and record["decision"] is not None  # and so does the answer


# --- the client's admission of evidence, one refusal at a time ---------------------------------

STATE = "a state of forty characters, more or less."


def said(spans=None, status=None, **changes):
    """A reply with evidence for one question, as a service with a head writes it."""
    import hashlib

    answer = {"type": "noul", "noul": 0.2}
    if spans is not None:
        answer["evidence"] = {"status": status or ("spans" if spans else "empty"), "spans": spans}
    about = {"state_sha256": hashlib.sha256(STATE.encode()).hexdigest(), "state_chars": len(STATE)}
    reply = {"answers": {XPIA: answer}, "evidence_available": True}
    return reply | {"evidence": about | {"head_sha256": HEAD}} | changes


def test_checked_intervals_of_the_state_that_was_sent_are_kept():
    assert kai.evidence(said([[0, 1], [1, 7], [30, len(STATE)]]), XPIA, STATE) == {
        "status": "spans",
        "spans": [[0, 1], [1, 7], [30, len(STATE)]],
        "head_sha256": HEAD,
        "edges": None,
        "state_sha256": said([])["evidence"]["state_sha256"],
        "readout": None,
        "intervals": None,
        "targets": None,
    }
    assert kai.evidence(said([]), XPIA, STATE)["status"] == "empty"
    assert kai.evidence(said([], "unavailable"), XPIA, STATE) == {
        "status": "unavailable",
        "spans": [],
    }
    none = {"answers": {XPIA: {"type": "noul", "noul": 0.2}}, "evidence_available": False}
    assert kai.evidence(none, XPIA, STATE) == {"status": "unavailable", "spans": []}


@pytest.mark.parametrize(
    "reply, why",
    [
        (said([[0, 3]], evidence_available=False), "evidence_from_a_service_that_has_none"),
        (said([[0, 3]], evidence=None), "evidence_missing"),
        (said(None), "evidence_missing"),
        (
            said(
                [[0, 3]],
                evidence={"state_sha256": "0" * 64, "state_chars": 42, "head_sha256": HEAD},
            ),
            "evidence_of_another_state",
        ),
        (
            said([[0, 3]]) | {"evidence": said([])["evidence"] | {"state_chars": 3}},
            "evidence_of_another_state",
        ),
        (
            said([[0, 3]]) | {"evidence": said([])["evidence"] | {"head_sha256": "head"}},
            "head_not_named",
        ),
        (said([[0, 3]], "empty"), "evidence_malformed"),
        (said([], "spans"), "evidence_malformed"),
        (said([[0, 3]], "certain"), "evidence_malformed"),
        (said({"0": 3}), "evidence_malformed"),
        (said([[0, 3]], "error"), "service"),
        (said([[3, 3]]), "span_outside_the_state_or_out_of_order"),
        (said([[5, 2]]), "span_outside_the_state_or_out_of_order"),
        (said([[-1, 2]]), "span_outside_the_state_or_out_of_order"),
        (said([[0, len(STATE) + 1]]), "span_outside_the_state_or_out_of_order"),
        (said([[4, 9], [8, 12]]), "span_outside_the_state_or_out_of_order"),
        (said([[9, 12], [0, 3]]), "span_outside_the_state_or_out_of_order"),
        (said([[0.0, 3]]), "span_outside_the_state_or_out_of_order"),
        (said([[False, 3]]), "span_outside_the_state_or_out_of_order"),
        (said([[0, 3, 5]]), "span_outside_the_state_or_out_of_order"),
        (said(["03"]), "span_outside_the_state_or_out_of_order"),
        (said([[i, i + 1] for i in range(33)]), "too_many_spans"),
    ],
)
def test_evidence_that_cannot_be_checked_is_an_error_and_keeps_no_interval(reply, why):
    assert kai.evidence(reply, XPIA, STATE) == {"status": "error", "spans": [], "why": why}
    # The same reply read for another state is of another state, whatever else is right.
    assert kai.evidence(said([[0, 3]]), XPIA, STATE + "!")["why"] == "evidence_of_another_state"


# --- what the record refuses to call evidence ----------------------------------------------------


def test_a_span_on_session_text_of_unknown_origin_or_from_after_the_call_is_an_error(
    spool, served, monkeypatch
):
    identity, maps = two_sessions(), {}
    quiet = served(default=[], session=[])
    judged(identity, quiet.routed(), monkeypatch, maps)
    (state,) = set(quiet.services[K3].evidence.seen)
    (found,) = [m for m in maps.values() if m is not None]
    unknown = next(s["at"] for s in found.segments if s["kind"] == "unmapped")
    _, record, _ = judged(identity, served(default=[], session=[unknown]).routed())
    assert record["spans"][DECISION] == {"status": "error", "why": "unmapped_source"} | {
        "package_sha256": K3,
        "head_sha256": HEAD,
        "state_sha256": found.sha256,
        "readout": "stub",
    }
    # The same map with the call moved earlier: the request it marked is now after the call.
    late = next(
        s for s in found.segments if s.get("session") == "s-late" and s.get("trust") == "user"
    )
    found.cutoff = {"session": "s-late", "seq": late["seq"] - 1}
    told = escalate.spans_record(
        {DECISION: {"status": "spans", "spans": [late["at"]]}}, lambda: found
    )
    assert told[DECISION] == {"status": "error", "why": "evidence_after_the_call"}
    told = escalate.spans_record(
        {DECISION: {"status": "spans", "spans": [late["at"]]}}, lambda: None
    )
    assert told[DECISION] == {"status": "error", "why": "no_source_map"}
    # Words this server wrote, a rule and the call resolve to what they are, not to a session.
    rule = next(s for s in found.segments if s["kind"] == "policy" and s.get("rule"))
    call = next(s for s in found.segments if s["kind"] == "call" and s.get("field") == "arguments")
    plan = sorted([rule["at"], [call["at"][0] + 1, call["at"][0] + 8], [0, 5]])
    _, record, _ = judged(identity, served(default=plan, session=[]).routed())
    kinds = [[p["kind"] for p in span["sources"]] for span in record["spans"][XPIA]["spans"]]
    assert kinds == [["synthesized"], ["policy"], ["call"]]
    assert record["spans"][XPIA]["spans"][2]["sources"][0]["source"] == [1, 8]
    assert state[call["at"][0] : call["at"][1]] == '{"command": "sh tools/submit.sh"}'


def test_one_checkpoint_alone_carries_both_questions_evidence(spool, served):
    one = served(default=[[0, 5]])
    _, record, _ = judged(two_sessions(), one.default)
    assert {q: (e["status"], e["package_sha256"]) for q, e in record["spans"].items()} == {
        DECISION: ("spans", K0),
        XPIA: ("spans", K0),
    }


def test_the_map_names_delimiters_and_where_one_source_ends(spool, served, monkeypatch):
    identity, maps = two_sessions(), {}
    quiet = served(default=[], session=[])
    judged(identity, quiet.routed(), monkeypatch, maps)
    (state,) = set(quiet.services[K3].evidence.seen)
    (found,) = [m for m in maps.values() if m is not None]
    quotes = [s for s in found.segments if s.get("role") == "quote"]
    assert quotes and all(state[s["at"][0] : s["at"][1]] == '"' for s in quotes)
    edges = set(found.boundaries())
    # A quoted value ends where its closing mark begins, and nothing else lies inside it.
    assert all(
        quote["at"][0] in edges and quote["at"][1] in edges | {found.chars} for quote in quotes
    )
    for segment in found.segments:
        if segment["kind"] in ("policy", "host_fact", "call"):
            assert segment["at"][0] in edges and segment["at"][1] in edges | {found.chars}
        assert "trust" in segment or segment["kind"] != "session"


def test_a_state_whose_token_places_cannot_be_trusted_gets_an_error_not_a_guess():
    made = service(K0, [[0, 3]])
    ask = {"questions": {"q": {"type": "noul", "instructions": "check"}}}
    composed = made.evaluate(ask | {"state": "caf\u00e9 and more"})
    assert composed["answers"]["q"]["evidence"] == {
        "status": "spans",
        "spans": [[0, 3]],
        "edges": [[[0, 1], [2, 3]]],  # the tokens holding its two ends (one for a character)
    }
    assert composed["evidence"]["projection"] == "none"  # a service projects nothing
    assert composed["evidence"]["state_chars"] == 13 and composed["evidence"]["qualified"] is False
    apart = made.evaluate(ask | {"state": "cafe\u0301 and more"})  # e, then a combining accent
    assert apart["answers"]["q"]["evidence"] == {"status": "error", "spans": []}
    assert apart["answers"]["q"]["noul"] == composed["answers"]["q"]["noul"]  # the answer stands
    plain = service(K0).evaluate(ask | {"state": "cafe\u0301 and more"})
    assert "evidence" not in plain and "evidence" not in plain["answers"]["q"]
    assert plain["evidence_available"] is False


def test_evidence_of_an_earlier_session_is_placed_by_its_own_time_against_the_calls(
    spool, served, monkeypatch
):
    """A piece of another session is evidence only if its event is known to have come before
    the call: by the event's own time against the time that session was cut at."""
    identity, maps = two_sessions(), {}
    judged(identity, served(default=[], session=[]).routed(), monkeypatch, maps)
    (found,) = [m for m in maps.values() if m is not None]
    earlier = next(s for s in found.segments if s["kind"] == "session" and s["session"] != "s-late")
    cut = found.sessions[earlier["session"]]  # the cut the lookup made, kept with the map
    assert earlier["ts"] < cut["until"] and "call_time" not in cut["unknown"]

    def told():
        spans = {DECISION: {"status": "spans", "spans": [earlier["at"]]}}
        return escalate.spans_record(spans, lambda: found)[DECISION]

    assert told()["spans"][0]["sources"][0]["seq"] == earlier["seq"]
    found.sessions[earlier["session"]] = cut | {"until": earlier["ts"] - 1}
    assert told() == {"status": "error", "why": "evidence_after_the_call"}
    found.sessions[earlier["session"]] = cut | {"until": earlier["ts"]}  # at the very time
    assert told() == {"status": "error", "why": "evidence_order_unknown"}
    found.sessions[earlier["session"]] = cut | {"unknown": ["call_time"]}
    assert told() == {"status": "error", "why": "evidence_order_unknown"}
    del found.sessions[earlier["session"]]  # a session shown without its cut
    assert told() == {"status": "error", "why": "evidence_order_unknown"}


def test_what_summarizer_derived_is_kept_as_that_and_is_no_error():
    """A span over a string Summarizer made (an indicator's basis, a note, an observable) is
    resolved to the summary field it is and the event it is about: provenance of a derived
    string, not the session's own words, and not unknown."""
    from summarizer.sourcemap import SourceMap

    about = {"session": "s-late", "seq": 4, "type": "action", "field": "indicators[0].basis"}
    segment = about | {"kind": "derived", "trust": "agent", "at": [2, 8], "source": [0, 6]}
    found = SourceMap("x" * 10, [segment | {"exact": True}], cutoff={"session": "s-late", "seq": 5})
    told = escalate.spans_record({XPIA: {"status": "spans", "spans": [[3, 6]]}}, lambda: found)
    (piece,) = told[XPIA]["spans"][0]["sources"]
    assert (piece["kind"], piece["field"], piece["source"]) == ("derived", about["field"], [1, 4])
    found.cutoff = {"session": "s-late", "seq": 3}  # derived from an event after the call
    told = escalate.spans_record({XPIA: {"status": "spans", "spans": [[3, 6]]}}, lambda: found)
    assert told[XPIA] == {"status": "error", "why": "evidence_after_the_call"}
