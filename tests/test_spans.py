"""Evidence spans from a source to the state, through the service, the client, the route and
the gate's record, and back to the source. Stand-ins only: a stub readout, no model.

What is checked is the path and its refusals. No trained evidence head exists; nothing here
says a span is right."""

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
    assert record["spans"][XPIA] == {"status": "empty", "package_sha256": K0, "head_sha256": HEAD}
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
