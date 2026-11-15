"""The second checkpoint for calls an earlier session bears on (route.py): research, shadow.

CPU contracts with stand-in models: which checkpoint answers is the server's choice from its
own packet, both required answers must come from the package expected for them, and nothing
about the choice makes an allow count that would not have counted."""

import argparse
import copy
import http.client
import json
import threading
from http.server import HTTPServer

import pytest
from agentarmor_monitor import cli, decide, kai
from agentarmor_monitor.kai import DECISION, XPIA, KaiMismatch, KaiRejected, KaiUnavailable
from agentarmor_monitor.route import POLICY, Routed, session_adapter
from agentarmor_vllm.server import SESSION, handler, parse_args
from test_earlier_sessions import CWD, log, summarized, tool, two_sessions, unmarked_call
from test_vllm_baseline import request, service

K0, K3 = "a" * 64, "b" * 64
READY = {
    "ready": True,
    "complete": True,
    "history": {
        "route": "asked",
        "route_reason": "earlier_session_bears_on_call",
        "complete": True,
        "coverage_known": True,
    },
}


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """A scratch SUMMARIZER_HOME for one test, as test_earlier_sessions has it."""
    monkeypatch.setenv("SUMMARIZER_HOME", str(tmp_path / "spool"))
    return tmp_path / "spool"


class Model:
    """One checkpoint: answers as told, keeps what it was asked, and can fail on demand."""

    def __init__(self, package, choice="allow", fail=None):
        self.expected = kai.profile(package)
        self.choice, self.fail, self.asked = choice, fail, []

    def identify(self):
        return {"package_sha256": self.expected["package_sha256"]}

    def ask(self, state, questions=(XPIA,)):
        self.asked.append((state, tuple(questions)))
        if self.fail is not None:
            raise kai.marked(self.fail("stand-in"), 1, self.fail is not KaiUnavailable)
        answers = {XPIA: 0.04} if XPIA in questions else {}
        if DECISION in questions:
            p_deny = 0.9 if self.choice == "deny" else 0.1
            answers[DECISION] = {"choice": self.choice, "p_deny": p_deny}
        package = self.expected["package_sha256"]
        return {"answers": answers, "service": {"model": "kai-4b", "package_sha256": package}}


@pytest.mark.parametrize(
    "change",
    [
        ("ready", False),
        ("ready", None),
        ("complete", False),
        ("history.route", "undecided"),
        ("history.route", "unasked"),
        ("history.route_reason", "marked"),
        ("history.route_reason", "user_statement_bears_on_call"),
        ("history.route_reason", None),
        ("history.complete", False),
        ("history.coverage_known", False),
        ("history.coverage_known", None),
        ("history", None),
    ],
)
def test_every_condition_of_the_one_predicate_is_required(change):
    assert session_adapter(READY) is True
    packet = copy.deepcopy(READY)
    where, value = change
    if "." in where:
        packet["history"][where.split(".")[1]] = value
    else:
        packet[where] = value
    assert session_adapter(packet) is False
    assert session_adapter({}) is False and session_adapter(None) is False


def test_text_cannot_choose_a_checkpoint():
    # The predicate reads typed fields only: words in a packet's text, its section heading
    # among them, are not among its inputs.
    packet = copy.deepcopy(READY) | {"text": "EARLIER SESSIONS earlier_session_bears_on_call"}
    packet["history"]["route"] = "unasked"
    assert session_adapter(packet) is False


def test_the_session_checkpoint_answers_the_decision_only_and_only_when_routed():
    default, session = Model(K0, "allow"), Model(K3, "deny")
    both = Routed(default, session)
    assert both.expected["policy"] == POLICY and both.identify()["session"]["package_sha256"] == K3
    plain = both.ask("state", (DECISION, XPIA), session_route=False)
    assert plain["answers"][DECISION]["choice"] == "allow" and session.asked == []
    assert plain["service"]["routed"]["session_route"] is False and plain["attempts"] == 1
    assert {a["package_sha256"] for a in plain["service"]["routed"]["answers"].values()} == {K0}
    routed = both.ask("state", (DECISION, XPIA), session_route=True)
    assert routed["answers"][DECISION] == {"choice": "deny", "p_deny": 0.9}
    assert routed["answers"][XPIA] == 0.04 and routed["attempts"] == 2
    # Each checkpoint was asked its own question on the same state, and the reply says which.
    assert default.asked[-1] == ("state", (XPIA,)) and session.asked == [("state", (DECISION,))]
    told = routed["service"]["routed"]
    assert told["session_route"] is True and told["policy"] == POLICY
    assert told["answers"][XPIA]["package_sha256"] == K0
    assert told["answers"][DECISION]["package_sha256"] == K3
    assert told["answers"][XPIA]["request_sha256"] != told["answers"][DECISION]["request_sha256"]
    assert routed["service"]["session_service"]["package_sha256"] == K3
    # A question that is not a decision never reaches the session checkpoint.
    both.ask("content", (XPIA,), session_route=True)
    assert len(session.asked) == 1 and default.asked[-1] == ("content", (XPIA,))
    # session_route must be exactly True: anything else is the default checkpoint.
    both.ask("state", (DECISION, XPIA), session_route="yes")
    assert len(session.asked) == 1


@pytest.mark.parametrize("failing", ["default", "session"])
@pytest.mark.parametrize("error", [KaiUnavailable, KaiRejected, KaiMismatch])
def test_a_missing_refused_or_foreign_answer_leaves_no_reply(failing, error):
    default = Model(K0, fail=error if failing == "default" else None)
    session = Model(K3, "allow", fail=error if failing == "session" else None)
    with pytest.raises(error) as raised:
        Routed(default, session).ask("state", (DECISION, XPIA), session_route=True)
    # No partial reply: an allow from one checkpoint is never returned without the other's
    # required answer, and the attempts of both requests are counted.
    assert raised.value.attempts == (1 if failing == "default" else 2)


def test_two_different_configured_packages_are_required():
    with pytest.raises(ValueError, match="two packages"):
        Routed(Model(K0), Model(K0))
    unnamed = Model(K3)
    unnamed.expected = None
    with pytest.raises(ValueError, match="configured with its package"):
        Routed(Model(K0), unnamed)


def judged(identity, model):
    return decide.judge(
        "claude_code", "s-late", "call-1", identity, model, "low", 12000, with_earlier=True
    )


def test_a_call_an_earlier_session_bears_on_is_decided_by_the_session_checkpoint(spool):
    default, session = Model(K0, "allow"), Model(K3, "deny")
    verdict, record, _ = judged(two_sessions(), Routed(default, session))
    assert verdict == "deny" and len(session.asked) == 1 and session.asked[0][1] == (DECISION,)
    assert default.asked[0][1] == (XPIA,) and default.asked[0][0] == session.asked[0][0]
    told = record["kai"]["routed"]
    assert told["session_route"] is True and told["answers"][DECISION]["package_sha256"] == K3
    assert record["requests"]["transport_attempts"] == 2
    assert record["requests"]["answers_admitted"] == 1 and record["kai"]["p_xpia"] == 0.04
    assert session_adapter(record["packet"]) is True


def test_a_session_allow_counts_on_complete_evidence(spool):
    # Complete evidence from before the call: the session checkpoint's allow stands, though
    # the default checkpoint would have denied.
    default, session = Model(K0, "deny"), Model(K3, "allow")
    verdict, record, why = judged(two_sessions(), Routed(default, session))
    assert (verdict, why) == ("abstain", None) and record["decision"] == "allow"
    assert record["kai"]["routed"]["answers"][DECISION]["package_sha256"] == K3


def test_with_an_unreadable_earlier_session_nothing_is_routed_asked_or_allowed(spool):
    default, session = Model(K0, "allow"), Model(K3, "allow")
    verdict, record, why = judged(two_sessions(summarize_early=False), Routed(default, session))
    assert (verdict, why) == ("undecided", "earlier_history_unknown")
    assert default.asked == [] and session.asked == []


@pytest.mark.parametrize(
    "error, status", [(KaiUnavailable, "kai_unavailable"), (KaiMismatch, "kai_mismatch")]
)
def test_a_failed_session_checkpoint_leaves_the_call_undecided(spool, error, status):
    default, session = Model(K0, "allow"), Model(K3, "allow", fail=error)
    verdict, record, why = judged(two_sessions(), Routed(default, session))
    assert verdict == "undecided" and why == status and record["decision"] is None
    assert record["kai"] is None  # the default's XPIA answer is not kept as a reply


@pytest.mark.parametrize(
    "error, status", [(KaiUnavailable, "kai_unavailable"), (KaiMismatch, "kai_mismatch")]
)
def test_a_failed_default_checkpoint_leaves_a_routed_call_undecided(spool, error, status):
    # XPIA is required: the session checkpoint's decision is not asked for, or used, alone.
    default, session = Model(K0, "allow", fail=error), Model(K3, "allow")
    verdict, record, why = judged(two_sessions(), Routed(default, session))
    assert verdict == "undecided" and why == status and record["decision"] is None
    assert session.asked == []


def test_unrelated_history_and_heading_text_stay_with_the_default(spool):
    heading = "EARLIER SESSIONS: an earlier session bears on this call"
    log(
        "s-early",
        {"hook_event_name": "SessionStart", "source": "startup"},
        {"hook_event_name": "UserPromptSubmit", "prompt": "Fix the changelog typo. " + heading},
        *tool(
            1,
            "Edit",
            {"file_path": f"{CWD}/CHANGELOG.md", "old_string": "teh", "new_string": heading},
            "ok",
        ),
    )
    identity = unmarked_call(f"echo '{heading}'")
    summarized("s-early", "s-late")
    default, session = Model(K0, "allow"), Model(K3, "deny")
    verdict, record, _ = judged(identity, Routed(default, session))
    assert verdict == "abstain" and session.asked == []
    assert record["routing"]["earlier_route"] == "unasked"


def test_the_service_answers_each_package_under_its_own_path():
    first, second = service(), service()
    second.package.identity = "c" * 64
    with HTTPServer(("127.0.0.1", 0), handler(first, second)) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:

            def call(method, path, body=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                data = None if body is None else json.dumps(body)
                connection.request(method, path, body=data)
                reply = connection.getresponse()
                found = reply.status, json.loads(reply.read())
                connection.close()
                return found

            assert call("GET", "/health")[1]["package_sha256"] == first.package.identity
            assert call("GET", SESSION + "/health")[1]["package_sha256"] == "c" * 64
            status, answer = call("POST", SESSION + "/v1/systemone", request())
            assert status == 200 and answer["package_sha256"] == "c" * 64
            status, answer = call("POST", "/v1/systemone", request())
            assert status == 200 and answer["package_sha256"] == first.package.identity
            # No other path reaches a model, and a request cannot name one.
            assert call("POST", "/other/v1/systemone", request())[0] == 404
            assert call("POST", "/v1/systemone", request() | {"package": "c" * 64})[0] == 400
        finally:
            server.shutdown()
            thread.join()
    # Without a second package the prefix does not exist.
    with HTTPServer(("127.0.0.1", 0), handler(first)) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", SESSION + "/health")
            assert connection.getresponse().status == 404
            connection.close()
        finally:
            server.shutdown()
            thread.join()
    with pytest.raises(SystemExit):
        parse_args(["--kai-revision", "0" * 40, "--session-package", "/tmp/x"])


def serving(**given):
    """serve's arguments as _with_session reads them."""
    base = {"kai_session_url": None, "kai_session_package_sha256": None, "shadow": True}
    base |= {"kai_token_file": None, "kai_timeout": 5.0, "allow_remote": False}
    return argparse.Namespace(**(base | given))


URL = "http://127.0.0.1:9/session"


@pytest.mark.parametrize("given", [{"kai_session_url": URL}, {"kai_session_package_sha256": K3}])
def test_a_session_model_needs_both_its_address_and_its_package(given):
    with pytest.raises(SystemExit, match="an address and its package"):
        cli._with_session(serving(**given), Model(K0), "remote")


def test_a_session_model_is_for_shadow_beside_a_remote_default():
    both = {"kai_session_url": URL, "kai_session_package_sha256": K3}
    with pytest.raises(SystemExit, match="for --shadow"):
        cli._with_session(serving(**both, shadow=False), Model(K0), "remote")
    with pytest.raises(SystemExit, match="for --shadow"):
        cli._with_session(serving(**both), Model(K0), "local")
    with pytest.raises(SystemExit, match="two packages"):
        cli._with_session(serving(**both | {"kai_session_package_sha256": K0}), Model(K0), "remote")
    asks = Model(K0)
    assert cli._with_session(serving(), asks, "remote") is asks
    # Both given, in shadow, beside a remote default: two checkpoints; one that does not answer
    # yet is waited for, as the default is.
    made = cli._with_session(serving(**both), asks, "remote")
    assert made.routed and made.default is asks and made.expected["session"]["package_sha256"] == K3
