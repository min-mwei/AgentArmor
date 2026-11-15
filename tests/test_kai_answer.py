"""A model reply counts only if it says one thing (kai._decision, kai._probability).

A decision whose named choice its own probabilities do not support, or that leaves an option
out, or whose probabilities are not a distribution, is no answer: asked directly, through the
server's own model (relay and in-process) or through two checkpoints, the call stays
undecided and nothing is allowed. Stand-in transports only; no model."""

import math

import pytest
from agentarmor_monitor import decide, kai
from agentarmor_monitor.kai import DECISION, XPIA, KaiClient, KaiUnavailable
from agentarmor_monitor.route import Routed
from agentarmor_monitor.server import Front, InProcess, Relay
from test_earlier_sessions import two_sessions

K0, K3 = "a" * 64, "b" * 64


@pytest.fixture
def spool(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMMARIZER_HOME", str(tmp_path / "spool"))
    return tmp_path / "spool"


def decision(choice, **odds):
    return {"type": "choice", "choice": choice, "confidence": 0.5, "probabilities": odds}


GOOD = decision("allow", allow=0.9, deny=0.1)
BAD = {
    "contradicts": decision("allow", allow=0.01, deny=0.99),
    "contradicts_deny": decision("deny", allow=0.99, deny=0.01),
    "no_allow": decision("deny", deny=0.99),
    "no_allow_named_allow": decision("allow", deny=0.99),
    "not_a_distribution": decision("allow", allow=0.9, deny=0.9),
    "sums_low": decision("deny", allow=0.1, deny=0.2),
    "extra_option": decision("allow", allow=0.8, deny=0.1, ask=0.1),
    "tie_named_deny": decision("deny", allow=0.5, deny=0.5),
    "nan": decision("deny", allow=math.nan, deny=0.99),
    "integer": decision("deny", allow=0, deny=1),
    "boolean": decision("deny", allow=False, deny=True),
    "text": decision("deny", allow="0.01", deny="0.99"),
    "other_type": decision("allow", allow=0.9, deny=0.1) | {"type": "noul"},
    "no_type": {"choice": "allow", "probabilities": {"allow": 0.9, "deny": 0.1}},
    "unknown_choice": decision("permit", allow=0.9, deny=0.1),
    "no_probabilities": {"type": "choice", "choice": "allow"},
}


def reply(package, answers):
    said = {"coverage": "complete", "answers": answers, "policy_ready": False}
    return said | {key: kai.profile(package)[key] for key in kai.IDENTITY}


def client(package, **answers):
    """A real client whose transport alone is a stand-in: it reads what a service sent."""
    made = KaiClient(expected=kai.profile(package))
    full = {XPIA: {"type": "noul", "noul": 0.99}, DECISION: GOOD} | answers
    made._call = lambda path, body=None: reply(
        package, {q: full[q] for q in (body or {}).get("questions", full)}
    )
    return made


def in_process(package, **answers):
    """The server's own way to a model at another address: InProcess over Front over Relay."""
    return InProcess(Front(Relay(client(package, **answers)), "remote", None, kai.profile(package)))


def judged(identity, model):
    return decide.judge(
        "claude_code", "s-late", "call-1", identity, model, "low", 12000, with_earlier=True
    )


def test_a_consistent_decision_is_read_as_it_was_given():
    found = client(K0).ask("state", (XPIA, DECISION))["answers"]
    assert found == {XPIA: 0.99, DECISION: {"choice": "allow", "p_deny": 0.1}}
    denied = decision("deny", allow=0.25, deny=0.75)
    assert client(K0, decision=denied).ask("s", (DECISION,))["answers"][DECISION] == {
        "choice": "deny",
        "p_deny": 0.75,
    }
    # The service's own rule on an exact tie: the first option in sorted order, allow.
    tie = decision("allow", allow=0.5, deny=0.5)
    assert client(K0, decision=tie).ask("s", (DECISION,))["answers"][DECISION]["choice"] == "allow"
    # float32 probabilities need not sum to one exactly.
    near = decision("deny", allow=0.3000001, deny=0.7000001)
    assert client(K0, decision=near).ask("s", (DECISION,))["answers"][DECISION]["p_deny"] > 0.7


@pytest.mark.parametrize("name", sorted(BAD))
@pytest.mark.parametrize("way", [client, in_process])
def test_an_inconsistent_decision_is_no_answer(name, way):
    with pytest.raises(KaiUnavailable) as raised:
        way(K0, decision=BAD[name]).ask("state", (XPIA, DECISION))
    assert raised.value.attempts == 1 and raised.value.responded is True


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "noul", "noul": math.nan},
        {"type": "noul", "noul": math.inf},
        {"type": "noul", "noul": 1.01},
        {"type": "noul", "noul": -0.01},
        {"type": "noul", "noul": 1},
        {"type": "noul", "noul": True},
        {"type": "noul", "noul": "0.5"},
        {"type": "noul"},
        {"type": "choice", "noul": 0.5},
        {"noul": 0.5},
    ],
)
def test_a_yes_no_answer_is_a_finite_probability_of_its_own_type(answer):
    with pytest.raises(KaiUnavailable):
        client(K0, is_xpia_attempt=answer).ask("state", (XPIA,))
    assert client(K0, is_xpia_attempt={"type": "noul", "noul": 0.0}).ask("s")["answers"][XPIA] == 0


@pytest.mark.parametrize("name", ["contradicts", "no_allow_named_allow", "not_a_distribution"])
@pytest.mark.parametrize("way", [client, in_process])
def test_at_the_gate_an_inconsistent_allow_leaves_the_call_undecided(spool, name, way):
    # The reviewer's case: complete evidence from before the call, the configured package
    # answering, and a reply that names allow against its own probabilities.
    identity = two_sessions()
    verdict, record, why = judged(identity, way(K0, decision=BAD[name]))
    assert (verdict, why) == ("undecided", "kai_unavailable")
    assert record["decision"] is None and record["kai"] is None
    # The same packet with a reply that says one thing is decided as before.
    verdict, record, why = judged(identity, way(K0))
    assert (verdict, why) == ("abstain", None) and record["decision"] == "allow"


@pytest.mark.parametrize("name", ["contradicts", "no_allow_named_allow", "not_a_distribution"])
def test_at_the_gate_a_routed_inconsistent_allow_leaves_the_call_undecided(spool, name):
    identity = two_sessions()
    both = Routed(client(K0), client(K3, decision=BAD[name]))
    verdict, record, why = judged(identity, both)
    assert (verdict, why) == ("undecided", "kai_unavailable") and record["decision"] is None
    # The default checkpoint's own inconsistent reply, were it the one deciding, likewise.
    with pytest.raises(KaiUnavailable):
        Routed(client(K0, decision=BAD[name]), client(K3)).ask("state", (XPIA, DECISION))
    verdict, record, _ = judged(identity, Routed(client(K0), client(K3)))
    assert verdict == "abstain" and record["decision"] == "allow"
    assert record["kai"]["routed"]["session_route"] is True
