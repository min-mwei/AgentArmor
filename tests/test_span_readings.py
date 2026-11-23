"""Strict and projected readings of a decoded evidence interval (agentarmor_monitor.spans).

Stand-ins only: a stub readout and synthetic states. What is checked is that a served interval
is kept as decoded, that its projected reading is the inverse of the training projection and
uses only marks the renderer wrote, and that identity survives every hop. Nothing here says a
span is right."""

import hashlib
import random
import re
from types import SimpleNamespace

import numpy as np
import pytest
import span_targets
from agentarmor_monitor import escalate, kai, spans
from agentarmor_monitor.kai import DECISION, XPIA
from agentarmor_vllm.encode import SPECIAL, tokenizer_sha256
from agentarmor_vllm.engine import Pointer, Service, token_edges
from summarizer.sourcemap import SourceMap
from test_earlier_sessions import two_sessions
from test_spans import HEAD, K0, Backbone, judged, served, spool  # noqa: F401

BOTH = {"rule": spans.WRAPPER, "wrapper_sources": ["quote", "call-argument-value"]}
QUOTES = {"rule": spans.WRAPPER, "wrapper_sources": ["quote"]}


# --- the inverse of the training projection, against the projection written from its rule -------


def projection(tokens, state, marks, start, end):
    """Kai's target projection of the annotated interval [start, end), from its definition:
    the hull of the tokens that overlap it, without white space at its ends; "exact" when that
    is the interval, "wrapper" when all it adds is the interval's own value's mark."""
    inside = [(a, b) for a, b in tokens if a < end and b > start]
    low, high = min(a for a, _ in inside), max(b for _, b in inside)
    while low < high and state[low].isspace():
        low += 1
    while high > low and state[high - 1].isspace():
        high -= 1
    if (low, high) == (start, end):
        return (low, high), "exact"
    if not (low <= start and end <= high):
        return None
    for at in (*range(low, start), *range(end, high)):
        held = marks.get(at)
        opening = held and at == start - 1 and held[0] == start and held[1] >= end
        closing = held and at == end and held[1] == end and held[0] <= start
        if state[at] != '"' or not (opening or closing):
            return None
    return (low, high), "wrapper"


def a_state(rng):
    """(state, tokens, marks): random text, a random tokenization of all of it, and marks
    around some of its quoted runs (the others are content)."""
    state = "".join(rng.choice('ab" ') for _ in range(rng.randint(4, 16)))
    cuts = sorted({0, len(state), *(rng.randrange(len(state)) for _ in range(rng.randint(1, 8)))})
    tokens = list(zip(cuts, cuts[1:], strict=False))
    marks = {}
    for found in re.finditer(r'"([^"]+)"', state):
        if rng.random() < 0.7 and found.start() not in marks:
            marks[found.start()] = (found.start(1), found.end(1))
            if rng.random() < 0.8:  # a quotation that was cut has no closing mark
                marks[found.end(1)] = (found.start(1), found.end(1))
    return state, tokens, marks


def test_a_projected_reading_is_exactly_what_the_training_projection_would_have_widened():
    rng, seen = random.Random(20261115), {"exact": 0, spans.WRAPPER: 0, None: 0}
    for _ in range(1500):
        state, tokens, marks = a_state(rng)
        for first in range(len(tokens)):
            for last in range(first, len(tokens)):
                low, high = tokens[first][0], tokens[last][1]
                while low < high and state[low].isspace():
                    low += 1
                while high > low and state[high - 1].isspace():
                    high -= 1
                if low == high:
                    continue
                (edges,) = token_edges([[low, high]], [(0, 0), *tokens])
                read = spans.value([low, high], state, marks, edges)
                widened = {
                    (a, b)
                    for a in range(low, high)
                    for b in range(a + 1, high + 1)
                    if not state[a].isspace() and not state[b - 1].isspace()
                    if projection(tokens, state, marks, a, b) == ((low, high), "wrapper")
                }
                seen[read["rule"]] += 1
                if read["rule"] == spans.WRAPPER:
                    assert widened == {tuple(read["value"])} and read["marks"]
                    assert all(at in marks and state[at] == '"' for at in read["marks"])
                else:
                    assert not widened
                    assert read["rule"] is None or read == {"rule": "exact"} | {
                        "value": [low, high],
                        "marks": [],
                    }
    assert min(seen.values()) > 200  # every kind of reading was met


# --- a state made by hand: session quotations, an escaped quote, the proposed call ---------------

SAID, NEXT, CALL = 'say \\"hi\\" now', "then stop", '{"command": "sh run.sh", "note": "a \\"b\\""}'
STATE = f'user "{SAID}" tool "{NEXT}"\ncall {CALL}'


def mapped(state=STATE):
    """The map of STATE as the renderer would have made it: two quoted session strings (the
    first holds escaped quotation marks, which are content) and the call's arguments."""
    segments, about = [], {"kind": "session", "session": "s", "field": "text", "exact": True}
    for seq, (text, trust) in enumerate(((SAID, "user"), (NEXT, "tool")), start=1):
        at = STATE.index(f'"{text}"')
        one = about | {"seq": seq, "type": "prompt", "trust": trust}
        segments += [
            {"at": [at, at + 1], "kind": "synthesized", "role": "quote"},
            one | {"at": [at + 1, at + 1 + len(text)], "source": [0, len(text)]},
            {"at": [at + 1 + len(text), at + 2 + len(text)], "kind": "synthesized"}
            | {"role": "quote"},
        ]
    at = STATE.index(CALL)
    call = {"kind": "call", "field": "arguments", "exact": True, "source": [0, len(CALL)]}
    segments.append(call | {"at": [at, at + len(CALL)]})
    found = SourceMap(STATE, segments, cutoff={"session": "s", "seq": 9})
    found.sha256 = hashlib.sha256(state.encode()).hexdigest()
    return found


class Joined:
    """A tokenizer that joins a quotation mark to the character beside it, as the registered
    one does with a value's first and last characters; every other character is a token."""

    unk_token_id = -1
    backend_tokenizer = SimpleNamespace(to_str=lambda: "synthetic-joined-tokenizer/v1")
    pattern = re.compile(r'"[^\s"]|[^\s"]"|.', re.S)

    def convert_tokens_to_ids(self, text):
        return SPECIAL.index(text) + 1

    def __call__(self, text, **_):
        places = [found.span() for found in self.pattern.finditer(text)]
        ids = [sum(ord(c) for c in text[a:b]) + 10 for a, b in places]
        return SimpleNamespace(input_ids=ids, offset_mapping=places)


class Apart(Joined):
    """One token for each character: no mark is ever joined to a value."""

    backend_tokenizer = SimpleNamespace(to_str=lambda: "synthetic-apart-tokenizer/v1")
    pattern = re.compile(".", re.S)


def service(plan, targets=BOTH, tokenizer=Joined):
    tok = tokenizer()
    meta = {"tokenizer_sha256": tokenizer_sha256(tok), "pauses": 0, "pause_id": None}
    meta |= {"max_state": 4096, "max_row": 6000, "max_packed": 9000, "temperature": 1.0}
    evidence = {"readout": "stub"} | ({"targets": targets} if targets else {})
    about = SimpleNamespace(meta=meta, identity=K0, evidence=evidence)
    pointer = Pointer({"q.weight": [[0, 0]], "q.bias": [2], "k.weight": [[1, 0]], "k.bias": [1]}, 2)
    made = Service(about, tok, pointer, Backbone())
    made.evidence, made.head_sha256 = Marker(plan), HEAD
    return made


class Marker:
    """A readout that marks what it is told to."""

    def __init__(self, plan):
        self.plan = plan

    def spans(self, hidden, row, offsets, state):
        return self.plan


def hop(plan, state=STATE, **options):
    """A plan of intervals through the service, the client's admission and the monitor's
    record, with the identity each hop kept: (reply, admitted, recorded)."""
    ask = {"questions": {XPIA: {"type": "noul", "instructions": "check"}}, "state": state}
    reply = service(plan, **options).evaluate(ask)
    admitted = kai.evidence(reply, XPIA, state) | {"package_sha256": K0}
    record = escalate.spans_record({XPIA: admitted}, lambda: mapped(state), state)[XPIA]
    return reply, admitted, record


def at(text, inside=None):
    """Where text stands in STATE (or, with inside, where inside stands within it)."""
    start = STATE.index(text)
    if inside is None:
        return [start, start + len(text)]
    low = start + text.index(inside)
    return [low, low + len(inside)]


def test_a_value_and_its_own_marks_keep_both_readings_and_every_identity():
    whole, value = at(f'"{NEXT}"'), at(f'"{NEXT}"', NEXT)
    reply, admitted, record = hop([whole])
    digest = hashlib.sha256(STATE.encode()).hexdigest()
    # The service: the decoder's interval as it is, its tokens' places, and no projection.
    assert reply["answers"][XPIA]["evidence"] == {
        "status": "spans",
        "spans": [whole],
        "edges": [[[whole[0], whole[0] + 2], [whole[1] - 2, whole[1]]]],
    }
    about = reply["evidence"]
    assert (about["projection"], about["intervals"], about["targets"]) == ("none", None, BOTH)
    assert (about["state_sha256"], about["head_sha256"]) == (digest, HEAD)
    assert reply["policy_ready"] is False and about["qualified"] is False
    # The client: the same interval, the same state, head and package.
    assert admitted["spans"] == [whole] and admitted["targets"] == BOTH
    assert (admitted["state_sha256"], admitted["head_sha256"]) == (digest, HEAD)
    # The record: strict as decoded, and beside it the value under the head's rule.
    assert record["status"] == "spans" and record["strict"]["rule"] == "exact"
    assert [span["at"] for span in record["spans"]] == [whole]
    assert [p["kind"] for p in record["spans"][0]["sources"]] == [
        "synthesized",
        "session",
        "synthesized",
    ]
    read = record["projected"]
    assert read["rule"] == spans.WRAPPER and read["layout"]["state_sha256"] == digest
    assert read["layout"]["wrapper_sources"] == BOTH["wrapper_sources"]
    (one,) = read["spans"]
    assert (one["at"], one["value"], one["rule"]) == (whole, value, spans.WRAPPER)
    assert one["marks"] == [whole[0], whole[1] - 1]
    (piece,) = one["sources"]
    assert (piece["seq"], piece["trust"], piece["source"]) == (2, "tool", [0, len(NEXT)])
    kept = (record["state_sha256"], record["head_sha256"], record["package_sha256"])
    assert kept == (digest, HEAD, K0) and "edges" not in record
    assert NEXT not in str(record)  # identities and offsets, no text


def test_a_quotation_mark_that_is_content_is_never_taken_for_a_mark():
    inner = at(SAID, '\\"hi\\"')  # the escaped marks inside the user's words
    quoted = [inner[0] + 1, inner[1]]  # from the first inner mark to the second
    _, _, record = hop([quoted])
    (one,) = record["projected"]["spans"]
    assert STATE[quoted[0]] == '"' and STATE[quoted[1] - 1] == '"'
    assert (one["rule"], one["value"], one["marks"]) == ("exact", quoted, [])
    # A mark escaped inside a value of the call's arguments is content too, and a key's
    # marks are no value's.
    escaped = at(CALL, 'a \\"b\\"')
    key = at(CALL, '"note"')
    _, _, record = hop([key, [escaped[0] + 3, escaped[1]]])
    assert [(one["rule"], one["marks"]) for one in record["projected"]["spans"]] == [
        ("exact", []),
        ("exact", []),
    ]


def test_a_mark_of_another_value_is_never_taken_or_crossed():
    first, second = at(f'"{SAID}"'), at(f'"{NEXT}"')
    plans = {
        "from one value's opening mark into the next": [first[0], second[0] + 3],
        "from a value's closing mark on": [first[1] - 1, second[0] - 1],
        "into the next value's opening mark": [first[0] + 2, second[0] + 1],
        "a mark alone": [second[0], second[0] + 1],
    }
    for what, span in plans.items():
        _, _, record = hop([span])
        (one,) = record["projected"]["spans"]
        assert one["rule"] is None and "value" not in one and "sources" not in one, what
        assert one["why"] in ("mark_of_another_value", "only_marks", "mark_not_joined"), what
        assert [kept["at"] for kept in record["spans"]] == [span], what  # the strict one stands


def test_the_calls_own_value_marks_are_marks_only_for_a_head_whose_targets_used_them():
    whole, value = at(CALL, '"sh run.sh"'), at(CALL, "sh run.sh")
    _, _, record = hop([whole])
    (one,) = record["projected"]["spans"]
    assert (one["rule"], one["value"], one["sources"][0]["kind"]) == (spans.WRAPPER, value, "call")
    assert one["sources"][0]["source"] == [value[0] - at(CALL)[0], value[1] - at(CALL)[0]]
    _, _, record = hop([whole], targets=QUOTES)
    (one,) = record["projected"]["spans"]
    assert (one["rule"], one["value"]) == ("exact", whole)
    assert record["projected"]["layout"]["wrapper_sources"] == ["quote"]


def test_without_a_rule_a_map_the_state_or_joined_tokens_the_result_is_strict_only():
    whole = at(f'"{NEXT}"')
    _, admitted, record = hop([whole], targets=None)
    assert "projected" not in record and "targets" not in record
    assert [span["at"] for span in record["spans"]] == [whole] and record["strict"]
    _, admitted, _ = hop([whole])
    told = escalate.spans_record({XPIA: admitted}, lambda: mapped())[XPIA]  # no state given
    assert "projected" not in told and [span["at"] for span in told["spans"]] == [whole]
    assert spans.projected([whole], STATE, None, BOTH) is None
    # A rule or a source of marks this code does not have is named, not applied.
    other = {"rule": "serializer-structure-v2", "wrapper_sources": ["quote"]}
    _, _, record = hop([whole], targets=other)
    assert record["projected"] == {"rule": "serializer-structure-v2", "status": "unknown_rule"}
    _, _, record = hop([whole], targets=BOTH | {"wrapper_sources": ["quote", "guess"]})
    assert record["projected"]["status"] == "unknown_rule"
    # A mark that is a token of its own was never part of a target: no value is read from it.
    _, _, record = hop([whole], tokenizer=Apart)
    assert record["projected"]["spans"] == [{"at": whole, "rule": None, "why": "mark_not_joined"}]
    # And without the service's token places nothing is assumed about them.
    told = escalate.spans_record({XPIA: admitted | {"edges": None}}, lambda: mapped(), STATE)
    assert told[XPIA]["projected"]["spans"] == [
        {"at": whole, "rule": None, "why": "no_token_edges"}
    ]


def test_a_state_that_is_not_in_composed_form_is_refused_whatever_the_head_was_fitted_with():
    apart = STATE.replace("then", "thén")  # e, then a combining accent
    reply, admitted, record = hop([at(f'"{NEXT}"')], state=apart)
    assert reply["answers"][XPIA]["evidence"] == {"status": "error", "spans": []}
    assert admitted["status"] == "error" and admitted["why"] == "service"
    assert record == {"status": "error", "why": "service", "package_sha256": K0}
    assert isinstance(reply["answers"][XPIA]["noul"], float)  # the answer stands


def test_identity_that_does_not_hold_at_a_hop_is_an_error_there():
    whole = at(f'"{NEXT}"')
    reply, admitted, _ = hop([whole])
    # A service cannot have projected: it holds no marks.
    claimed = reply | {"evidence": reply["evidence"] | {"projection": spans.WRAPPER}}
    assert kai.evidence(claimed, XPIA, STATE) == {
        "status": "error",
        "spans": [],
        "why": "projection_by_the_service",
    }
    for bad in ({"rule": 1, "wrapper_sources": []}, "value-wrapper-quote-v1", {"rule": "x"}):
        said = reply | {"evidence": reply["evidence"] | {"targets": bad}}
        assert kai.evidence(said, XPIA, STATE)["why"] == "targets_malformed"
    # Token places that do not hold the ends of their span.
    given = reply["answers"][XPIA]["evidence"]
    for edges in ([], [[[0, 1], [0, 1]]], [[[whole[0], whole[0] + 1]]], "x", [[[0.0, 1], [1, 2]]]):
        answer = reply["answers"][XPIA] | {"evidence": given | {"edges": edges}}
        said = reply | {"answers": {XPIA: answer}}
        assert kai.evidence(said, XPIA, STATE)["why"] == "edges_malformed", edges
    # A map of another state than the evidence names.
    other = mapped(STATE + "!")
    told = escalate.spans_record({XPIA: admitted}, lambda: other, STATE)[XPIA]
    assert (told["status"], told["why"]) == ("error", "map_of_another_state")
    with pytest.raises(ValueError):
        spans.wrappers(other, STATE, ["quote"])
    with pytest.raises(ValueError):
        spans.wrappers(mapped(), STATE, ["call-argument-value"])  # never without the quotes


def test_token_places_are_of_the_tokens_holding_a_spans_ends_or_there_are_none():
    offsets = [(0, 0), (0, 2), (2, 2), (2, 5), (6, 9)]
    assert token_edges([[1, 4], [6, 9]], offsets) == [[[0, 2], [2, 5]], [[6, 9], [6, 9]]]
    assert token_edges([[1, 6]], offsets) is None  # no token holds character 5
    assert token_edges([], offsets) == []


# --- on a real rendering: the same marks the head's targets were projected with ------------------


def test_the_monitors_marks_are_the_layout_the_targets_were_projected_with(
    spool,  # noqa: F811
    served,  # noqa: F811
    monkeypatch,
):
    identity, maps = two_sessions(), {}
    quiet = served(default=[], session=[])
    _, plain, _ = judged(identity, quiet.routed(), monkeypatch, maps)
    (state,) = set(quiet.services[K0].evidence.seen)
    (found,) = [m for m in maps.values() if m is not None]
    for sources, call_values in ((["quote"], False), (BOTH["wrapper_sources"], True)):
        layout = span_targets.layout(found, state, call_values=call_values)
        marks = spans.wrappers(found, state, sources)
        assert sorted([at, *held] for at, held in marks.items()) == layout["wrappers"]
        told = spans.identity(found, sources)
        assert told == {key: layout[key] for key in told} and marks
    # A value of the session and its two marks, through the gate: evidence changes no verdict,
    # and a packet is no more complete for having spans that resolve.
    opening = next(
        s["at"][0] for s in found.segments if s.get("role") == "quote" and s["at"][0] in marks
    )
    value = marks[opening]
    marking = served(default=[[opening, value[1] + 1]], session=[])
    _, record, _ = judged(identity, marking.routed())
    assert record["spans"][XPIA]["spans"][0]["at"] == [opening, value[1] + 1]
    assert "projected" not in record["spans"][XPIA]  # this package says nothing of its targets
    assert record["spans"][XPIA]["state_sha256"] == hashlib.sha256(state.encode()).hexdigest()
    for key in ("decision", "flagged", "packet", "policy_ready"):
        assert record[key] == plain[key]


def test_the_hidden_states_a_question_is_scored_on_are_untouched_by_any_of_this():
    made = service([at(f'"{NEXT}"')])
    ask = {"questions": {DECISION: {"type": "noul", "instructions": "check"}}, "state": STATE}
    with_head = made.evaluate(ask)
    made.evidence = None
    assert made.evaluate(ask)["answers"][DECISION] == {
        key: value for key, value in with_head["answers"][DECISION].items() if key != "evidence"
    }
    assert np.isfinite(with_head["answers"][DECISION]["noul"])
