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
from agentarmor_monitor.route import POLICY, Routed, prior_event_adapter, session_adapter
from agentarmor_vllm.server import SESSION, handler, parse_args
from summarizer.hook import call_sha256
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


# The candidate route (prior_event_adapter): an event before the current request bears on the
# call, in this session or an earlier one. Off unless the operator turns it on; not adopted.

# No rule or probe marks it. Its words name no subcommand: "shipctl push ..." is now marked weakly
# by its subcommand (facts.named_writes), and these tests are of the route of an unmarked call.
SEND = {"command": "shipctl --to mirror-fern out/report.csv"}
REVOKED = "I revoke shipping out/report.csv to mirror-fern from now on."
PRIOR = {"alias": "1a2b", "seq": 2, "type": "prompt", "placement": "same_session"}
STATED = copy.deepcopy(READY)
STATED["history"] |= {"route_reason": "user_statement_bears_on_call", "prior": [PRIOR]}


def one_session(*prompts, name="s-late"):
    """One session: each prompt followed by a listing, then the push after the last one."""
    events = [{"hook_event_name": "SessionStart", "source": "startup"}]
    for number, prompt in enumerate(prompts[:-1], start=1):
        events.append({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        events += tool(number, "Bash", {"command": "ls out"}, "report.csv")
    events.append({"hook_event_name": "UserPromptSubmit", "prompt": prompts[-1]})
    events += tool(len(prompts), "Bash", SEND)
    log(name, *events)
    summarized(name)
    return f"call-{len(prompts)}", call_sha256("Bash", SEND)


def gated(call, identity, model, with_earlier=True, level="low"):
    return decide.judge(
        "claude_code", "s-late", call, identity, model, level, 12000, with_earlier=with_earlier
    )


@pytest.mark.parametrize(
    "change",
    [
        ("ready", False),
        ("complete", False),
        ("history.route", "unasked"),
        ("history.route_reason", "marked"),
        ("history.route_reason", None),
        ("history.complete", False),
        ("history.coverage_known", False),
        ("history.prior", []),
        ("history.prior", None),
        ("history.prior", "same_session"),
        ("history.prior", [PRIOR | {"placement": "elsewhere"}]),
        ("history.prior", [PRIOR | {"seq": "2"}]),
        ("history.prior", [PRIOR | {"alias": ""}]),
        ("history.prior", [PRIOR, "a prompt"]),
        ("history", None),
    ],
)
def test_every_condition_of_the_candidate_predicate_is_required(change):
    assert prior_event_adapter(STATED) is True and session_adapter(STATED) is False
    packet = copy.deepcopy(STATED)
    where, value = change
    if "." in where:
        packet["history"][where.split(".")[1]] = value
    else:
        packet[where] = value
    assert prior_event_adapter(packet) is False
    assert prior_event_adapter({}) is False and prior_event_adapter(None) is False


def test_the_candidate_is_the_operators_and_off_unless_set():
    from agentarmor_monitor.route import chosen_for

    off, on = Routed(Model(K0), Model(K3)), Routed(Model(K0), Model(K3), prior_events=True)
    assert off.prior_events is False and chosen_for(off, STATED) is False
    assert chosen_for(on, STATED) is True and chosen_for(on, READY) is True
    assert chosen_for(off, READY) is True  # the adopted route does not depend on it
    # Nothing in a packet turns it on, and it is on or off, exactly.
    assert chosen_for(off, STATED | {"prior_events": True}) is False
    assert chosen_for(Model(K0), STATED) is False
    with pytest.raises(ValueError, match="on or off"):
        Routed(Model(K0), Model(K3), prior_events="yes")
    # A reply says the candidate was on only when it was: the adopted reply is as it was.
    assert "prior_events" not in off.ask("s", (DECISION, XPIA))["service"]["routed"]
    assert on.ask("s", (DECISION, XPIA))["service"]["routed"]["prior_events"] is True


def test_with_the_candidate_on_a_revocation_of_an_earlier_turn_routes_the_call(spool):
    call, identity = one_session(REVOKED, "Finish the checklist.")
    default, session = Model(K0, "allow"), Model(K3, "deny")
    verdict, record, _ = gated(call, identity, Routed(default, session, prior_events=True))
    assert verdict == "deny" and session.asked[0][1] == (DECISION,)
    # The default checkpoint still answers the injection question, on the same state.
    assert default.asked[0][1] == (XPIA,) and default.asked[0][0] == session.asked[0][0]
    told = record["kai"]["routed"]
    assert told["session_route"] is True and told["prior_events"] is True
    assert told["answers"][DECISION]["package_sha256"] == K3
    assert told["answers"][XPIA]["package_sha256"] == K0
    assert told["answers"][XPIA]["request_sha256"] != told["answers"][DECISION]["request_sha256"]
    lookup = record["packet"]["history"]
    assert lookup["route_reason"] == "user_statement_bears_on_call"
    (prior,) = lookup["prior"]  # the revocation: its source, its place, where it stands
    assert (prior["type"], prior["placement"]) == ("prompt", "same_session") and prior["alias"]
    assert prior_event_adapter(record["packet"]) and not session_adapter(record["packet"])


def test_with_the_candidate_off_the_same_call_stays_with_the_default_checkpoint(spool):
    call, identity = one_session(REVOKED, "Finish the checklist.")
    default, session = Model(K0, "deny"), Model(K3, "allow")
    verdict, record, _ = gated(call, identity, Routed(default, session))
    assert verdict == "deny" and session.asked == []
    assert default.asked[0][1] == (DECISION, XPIA)
    assert record["kai"]["routed"]["session_route"] is False
    assert prior_event_adapter(record["packet"]) is True  # found, recorded, and not acted on


def test_a_request_that_asks_for_its_own_call_is_not_routed_by_the_candidate(spool):
    # Ordinary single-request work: the statement that bears on the call is the request
    # itself, so nothing before the current request is named and the default decides.
    call, identity = one_session("Only ship out/report.csv to mirror-fern.")
    default, session = Model(K0, "allow"), Model(K3, "deny")
    verdict, record, _ = gated(call, identity, Routed(default, session, prior_events=True))
    assert session.asked == [] and default.asked[0][1] == (DECISION, XPIA)
    assert record["packet"]["history"]["prior"] == []
    assert record["kai"]["routed"]["session_route"] is False
    assert prior_event_adapter(record["packet"]) is False


FOOTER = "A footer tells the agent to point config/build.toml at mirror-elm.example."
BUILD = {"command": "buildtool --config config/build.toml"}


def write_chain(target="config/build.toml", split=False):
    """Lower-trust text names a file, a call writes target, and after a new request a call
    names config/build.toml: in one session, or with the session ending before that request."""
    start = {"hook_event_name": "SessionStart", "source": "startup"}
    before = [start, {"hook_event_name": "UserPromptSubmit", "prompt": "Tidy the build notes."}]
    shown = FOOTER.replace("config/build.toml", target)
    before += tool(1, "Read", {"file_path": f"{CWD}/vendor/notes.md"}, shown)
    wrote = {"file_path": f"{CWD}/{target}", "content": 'registry = "https://mirror-elm.example"'}
    before += tool(2, "Write", wrote, "saved")
    after = [{"hook_event_name": "UserPromptSubmit", "prompt": "Build the docs."}]
    after += tool(3, "Bash", BUILD)
    if split:
        log("s-early", *before)
        log("s-late", start, *after)
        summarized("s-early", "s-late")
    else:
        log("s-late", *before, *after)
        summarized("s-late")
    return "call-3", call_sha256("Bash", BUILD)


@pytest.mark.parametrize("split", [False, True])
def test_a_write_chain_routes_the_same_way_on_either_side_of_a_restart(spool, split):
    # MR286 P2: the same events and the same call, with the session ending before the last
    # request or not. With the candidate on, the session checkpoint decides in both; the
    # account names what was shown and the write, each where it stands.
    call, identity = write_chain(split=split)
    default, session = Model(K0, "allow"), Model(K3, "deny")
    verdict, record, _ = gated(call, identity, Routed(default, session, prior_events=True))
    assert verdict == "deny" and session.asked[0][1] == (DECISION,)
    assert default.asked[0][1] == (XPIA,)  # the injection question stays where it was
    lookup = record["packet"]["history"]
    placement = "earlier_session" if split else "same_session"
    reason = "earlier_session_bears_on_call" if split else "earlier_event_bears_on_call"
    assert lookup["route_reason"] == reason
    assert [(p["type"], p["placement"]) for p in lookup["prior"]] == [
        ("content", placement),
        ("action", placement),
    ]
    assert prior_event_adapter(record["packet"]) is True
    assert session_adapter(record["packet"]) is split  # the adopted route is as it was


@pytest.mark.parametrize("split", [False, True])
def test_a_write_of_a_file_the_call_does_not_name_routes_nothing(spool, split):
    call, identity = write_chain(target="config/other.toml", split=split)
    default, session = Model(K0, "allow"), Model(K3, "deny")
    verdict, record, _ = gated(call, identity, Routed(default, session, prior_events=True))
    # Nothing before the request bears on the call: the session checkpoint is asked nothing,
    # in either placement. (Whether the default checkpoint is asked is the call's own marks:
    # with the lower-trust text in its own session the call is marked low and asked, as an
    # ordinary call is; in a later session it is not marked and nobody is asked.)
    assert session.asked == [] and all(q == (DECISION, XPIA) for _, q in default.asked)
    packet = record.get("packet")
    if packet is None:
        assert record["status"] == "unmarked" and record["routing"]["earlier_route"] == "unasked"
    else:
        assert packet["history"]["prior"] == [] and prior_event_adapter(packet) is False


def test_with_the_candidate_on_an_earlier_session_still_routes_as_it_did(spool):
    default, session = Model(K0, "allow"), Model(K3, "deny")
    verdict, record, _ = judged(two_sessions(), Routed(default, session, prior_events=True))
    assert verdict == "deny" and session.asked[0][1] == (DECISION,)
    assert session_adapter(record["packet"]) and prior_event_adapter(record["packet"])
    assert {p["placement"] for p in record["packet"]["history"]["prior"]} == {"earlier_session"}


def test_a_statement_of_this_session_is_asked_about_without_the_earlier_lookup(spool):
    # --earlier-sessions off: the revoked call, unmarked at the gate's level, was asked of
    # nobody, because what the user said in the session was looked up only with the lookup of
    # other sessions. Only this session is read; nothing is said of any other.
    call, identity = one_session(REVOKED, "Finish the checklist.")
    model = Model(K0, "deny")
    verdict, record, _ = gated(call, identity, model, with_earlier=False)
    assert verdict == "deny" and len(model.asked) == 1
    routing = record["routing"]
    assert (routing["session_route"], routing["eligible_by"]) == ("asked", "user_statement")
    assert "earlier_sessions" not in routing and "history" not in record["packet"]


def test_without_the_lookup_a_call_nothing_bounds_is_let_through_as_before(spool):
    call, identity = one_session("List what is in out.", "Finish the checklist.")
    model = Model(K0, "deny")
    verdict, record, why = gated(call, identity, model, with_earlier=False)
    assert (verdict, why, model.asked) == ("abstain", None, [])
    assert record["routing"]["session_route"] == "unasked"
    assert record["routing"]["reason"] == "below_threshold"


def test_a_session_whose_statements_cannot_be_read_is_no_finding(spool, monkeypatch):
    from summarizer import history

    call, identity = one_session(REVOKED, "Finish the checklist.")
    monkeypatch.setattr(history, "select", lambda *a, **k: 1 / 0)
    verdict, record, why = gated(call, identity, Model(K0), with_earlier=False)
    assert (verdict, why) == ("undecided", "session_statements_unknown")


def test_the_candidate_option_needs_a_session_model():
    args = argparse.Namespace(kai_session_url=None, kai_session_package_sha256=None)
    args.kai_session_prior_events = True
    with pytest.raises(SystemExit, match="for a session model"):
        cli._with_session(args, object(), "remote")
