"""The gate: one hook for every event, which asks the local server about a tool call.

Every event is first recorded through Summarizer's hook, so nothing an agent reads or runs is
missed. For a tool call that is about to run (PreToolUse) the gate then asks AgentArmor's
local server to decide about it (local.py, decide.py). The gate names the call and sends
nothing else. The server waits for Summarizer's summary of the session up to that call and
looks at how the call was marked:

- not marked at the trigger level, in a session Summarizer scanned whole: the gate says
  nothing, and the agent's own permission flow applies;
- marked: the server puts the call to the decision model with its rules and Summarizer's
  packet. A "deny" is returned to the agent as a denial with the reason. An "allow" says
  nothing, provided it was given on complete evidence from before the call (see below).

The gate holds no rules, builds no request, talks to no decision model and has no credential
for one. The server it asks is on this machine, and any other address is refused.

A check nobody can stand behind is not a pass. That covers an event the gate cannot read or
record, a gate that cannot start, a local server that does not answer or answers about another
call, no summary of this exact call in time, gaps in what Summarizer captured, no decision
model behind the server, one that is not the model the server was configured for, a call too
large to show whole, an "allow" given on a packet that left key evidence out or already held
later events, a user who spoke while the answer was awaited, and a decision model that could
not be reached or refused the request. Each is denied with its reason, unless --on-error
abstain was chosen. Every such outcome is appended to the session's detections, so default
denials can be counted.

In shadow (--shadow, or a server that says its decisions are shadow ones) the gate asks and
records as always and stops nothing: the record says what it would have answered, and the
agent's own permission flow is all that applies.

What a gate records is what it answered. Whether the agent took the answer is not the
gate's to know: a client can end a hook at its own deadline, and another hook can change the
call afterwards. What the agent reports of the call next is tied to the answer in a record
of its own (observe), and a result reported for a call the gate answered "deny" for is on
record as that.

The decision model's serving path is unqualified (policy_ready false): this gate applies an
unqualified model's decisions and is not a claim of attack prevention.
"""

import contextlib
import fcntl
import json
import os
import sys
import time

from summarizer.hook import MAX_STDIN, call_sha256, handle
from summarizer.store import AGENTS, home, private_dir, session_dir

from .escalate import SCHEMA, append, provenance, read
from .local import bound_to, question

REPORTS = ("PostToolUse", "PostToolUseFailure", "PermissionDenied")  # what became of a call
DENIED = "AgentArmor: Kai denied this call under the session's rules"
UNDECIDED = "AgentArmor could not complete the check"
WHY = {
    "event": "the hook event could not be read",
    "recording": "the event could not be recorded",
    "setup": "the gate could not start",
    "server": "AgentArmor's local server did not answer for this call",
    "request": "the local server did not take the question",
    "busy": "the local server is deciding as many calls as it takes at once",
    "no_summary": "no summary of this exact call arrived in time",
    "coverage": "the session record has gaps",
    "no_decision_model": "no decision model is behind the local server",
    "incomplete": "Kai was shown incomplete evidence",
    "moved_on": "the session had moved on when the call was checked",
    "log_unread": "the session's log could not be read to its end when the answer came",
    "outcome_reported": "the call was already reported as run or refused when the answer came",
    "call_too_large": "the call is too large to show Kai whole",
    "policy_too_large": "the rules leave no room for the session",
    "kai_unavailable": "Kai did not answer",
    "kai_rejected": "Kai refused the request",
    "too_large_to_send": "the request is too large to send to Kai",
    "kai_mismatch": "the decision model that answered is not the one configured",
    "failed": "the check failed",
}


def denial(event, reason):
    """The agent's own shape for a refusal of this event, and the exit status that carries it."""
    if event == "PermissionRequest":
        output = {"hookEventName": event, "decision": {"behavior": "deny", "message": reason}}
    else:
        output = {"hookEventName": event, "permissionDecision": "deny"}
        output["permissionDecisionReason"] = reason
    return {"hookSpecificOutput": output}, 2


def read_event(raw):
    """(event name, payload) of a hook event, or (None, None) when it cannot be read whole.

    The name is never guessed from part of the input: a tool call too large to parse could
    carry any name inside its arguments, and must not be taken for an event that needs no check.
    Any failure to decode counts, not only bad syntax: input nested too deeply to parse is as
    unreadable as input that is not JSON.
    """
    try:
        if len(raw) > MAX_STDIN:
            return None, None
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, None
    if not isinstance(payload, dict) or not isinstance(payload.get("hook_event_name"), str):
        return None, None
    return payload["hook_event_name"], payload


def note(agent, error, where):
    """Keep a failure where the operator can find it. Best effort, and never raises.

    Only the step that failed and the error's class are written. An exception's text can hold
    the event itself (a decoding error carries the bytes it could not decode), and an event
    that failed before it was recorded was never redacted.
    """
    try:
        private_dir(home())
        path = home() / "gate-errors.log"
        mode = "a" if not path.exists() or path.stat().st_size < 1_000_000 else "w"
        named = agent if agent in AGENTS else "?"
        what = type(error).__name__
        if isinstance(error, OSError) and isinstance(error.errno, int):
            what += f" errno={error.errno}"
        with open(path, mode, encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{named}\t{where}\t{what}\n")
        os.chmod(path, 0o600)
    except Exception:
        pass


def outcome(agent, session, call_id, why, summary=None):
    """A record of a gate outcome for which Kai was never asked.

    summary is the one the outcome was read from, when there was one, and the record then says
    what embedded it and what marked it. With none, both are left unknown and not guessed.
    """
    record = {"schema": SCHEMA, "ts": round(time.time(), 3)}
    record |= {"key": f"gate:{call_id if isinstance(call_id, str) else ''}"}
    record |= {"agent": agent, "session": session, "status": why, "kai": None}
    record |= {"advisory": True, "policy_ready": False, "decision": None, "flagged": None}
    return record | provenance(summary)


@contextlib.contextmanager
def one_at_a_time(agent, session, wait=1.0):
    """Hold a session's audit for one read-and-append. Never raises, and never waits long.

    A report and an answer for one call are written by two hook processes. Each reads what
    is on record and then appends, and the append being atomic does not make the reading
    before it so: between the two, the other process can write. A lock on a file beside
    the detections makes the pair one step. A gate must not hang on it, so it is asked for
    without blocking for a moment and then gone without. It yields whether the lock was
    taken, and a record written under it says so ("locked"): its writer saw everything
    that is before it in the file, so no report and answer of one call miss each other.

    That is all the lock says. It orders the appends, not the arrivals: a report can come
    in, be stamped, and then wait for the lock behind an answer that was made later. The
    order two things happened in is taken from when each arrived and where each landed
    together (_sure), under the lock as without it, or said not to be known.
    """
    held, got = None, False
    try:
        directory = session_dir(agent, session)
        private_dir(directory)
        held = os.open(os.path.join(directory, "detections.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
    except Exception as error:
        note(agent, error, "locking")
    try:
        yield got  # whether what is written now is written in its true place
    finally:
        if held is not None:
            with contextlib.suppress(OSError):
                os.close(held)  # closing gives the lock up


def keep(agent, session, record):
    """Append a record to a session's detections. Best effort, and never raises."""
    if not isinstance(session, str) or not session:
        return
    try:
        directory = session_dir(agent, session)
        private_dir(directory)
        append(directory, record)
    except Exception as error:
        note(agent, error, "keeping")


def answered(
    agent,
    payload,
    asked,
    verdict,
    why,
    denied,
    on_error,
    receipt,
    would=None,
    shadow=False,
    began=None,
):
    """Put on record what this gate answered about one proposed call. Only a gate knows that.

    The server records the decision it issued. Whether that decision arrived, and what the
    gate then answered, is recorded here, by the process that answered: a gate that stopped
    waiting has answered (deny, or nothing if it was set to) whatever the server says later.
    The two records share the attempt and the call's ids, so they can be laid side by side,
    and only this one says "answered".

    It is the gate's answer and no more. Whether the agent received it in time and acted on
    it is not known here ("delivery": "unknown"): a client that ends the hook at its own
    deadline runs the call, and so does one in which another hook changes the call after
    this one answered. "would" is what the gate answers when it is not in shadow, so a
    shadow run can be counted as the enforcing run it stands in for. What became of the
    call is what the hooks report next (observe).
    """
    session, call_id = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session:
        return
    asked = asked or {}
    attempt = asked.get("attempt")
    bound = (receipt or {}).get("bound") or {}
    record = {"schema": SCHEMA, "ts": round(time.time(), 3), "status": "answered"}
    record |= {"key": f"answered:{call_id if isinstance(call_id, str) else ''}:{attempt or ''}"}
    record |= {"agent": agent, "session": session, "stage": "answered", "attempt": attempt}
    record |= {"call_id": call_id if isinstance(call_id, str) else None}
    try:  # the hash of the call this gate held, also when nothing else could be done with it
        record["call_sha256"] = call_sha256(payload.get("tool_name"), payload.get("tool_input"))
    except Exception:
        record["call_sha256"] = None
    record |= {"verdict": verdict, "why": why, "on_error": on_error}
    would = denied if would is None else would
    record |= {"mode": "shadow" if shadow else "enforce"}
    record |= {"answered": "deny" if denied else "none", "would": "deny" if would else "none"}
    record |= {"delivery": "unknown", "receipt": receipt is not None}
    if began is not None:  # from reading the event to having an answer for the agent
        record["elapsed_ms"] = round((time.monotonic() - began) * 1000)
    # What the receipt was bound to, carried into the record: None where there was none.
    record |= {"policy_sha256": (bound.get("policy") or {}).get("sha256")}
    record |= {"evidence": bound.get("evidence"), "request_sha256": bound.get("request_sha256")}
    record |= {"advisory": True, "policy_ready": False}
    with one_at_a_time(agent, session) as locked:  # the answer and its reports, as one
        record["locked"] = bool(locked)
        keep(agent, session, record)
        if isinstance(call_id, str) and call_id:
            reconcile(agent, session, call_id, record)


def _observed(agent, session, call_id, event, answer, order):
    """A record of what the agent reported of a call, beside the answer it is tied to."""
    answer = answer or {}
    record = {"schema": SCHEMA, "ts": round(time.time(), 3), "status": "observed"}
    record |= {"agent": agent, "session": session, "stage": "observed", "call_id": call_id}
    record |= {"attempt": answer.get("attempt"), "reported": event, "order": order}
    record |= {"answered": answer.get("answered"), "mode": answer.get("mode")}
    denied = answer.get("answered") == "deny"
    record["ran_after_deny"] = order == "after_answer" and event == "PostToolUse" and denied
    record["ran_before_answer"] = order == "before_answer" and event == "PostToolUse"
    return record | {"advisory": True, "policy_ready": False}


def observe(agent, payload, event):
    """Put on record what the agent reported of a call. Never raises.

    A gate's answer is not what happened. This ties the two: the report (a result, a
    failure, a refusal by the agent's own permissions) with the last answer a gate gave
    for that call, and says in which order they came ("order"):

    - "after_answer": an answer was on record that had been made before the report came.
      A result for a call the gate answered "deny" for means the call ran all the same
      ("ran_after_deny").
    - "unknown": an answer is on record, and it was made at the same instant as the report
      came or later. It got its place first while the report waited its turn. Which of the
      two happened first is not known, and nothing is made of the report.
    - "no_answer": no answer for that call is on record yet. The report is kept as it is,
      so that it is not lost if the answer is still being awaited, and so that a call for
      which no gate ever answered can be counted. When an answer is recorded it is tied to
      the report (reconcile) in a record of its own.

    The report is timed when it comes, before the lock is asked for and before anything
    on record is read: that is when the call had already run, and waiting for the lock
    must not move it.

    A failure or a refusal after a deny is left as reported: either can be how an agent
    reports the denial itself. A reported result is not proof of any effect.
    """
    session, call_id = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session or not isinstance(call_id, str) or not call_id:
        return
    began = round(time.time(), 3)  # when the report came, before anything on record is read
    try:
        with one_at_a_time(agent, session) as locked:  # what is on record, and this report
            mine = [
                record
                for record in read(session_dir(agent, session))
                if record.get("stage") == "answered" and record.get("call_id") == call_id
            ]
            # An answer on record came before this report only if it was also made before
            # the report arrived. One made at the same instant or later got its place in
            # the file while this report waited its turn, and says nothing of the order.
            before = [answer for answer in mine if _earlier(answer.get("ts"), began)]
            last = before[-1] if before else None
            order = "after_answer" if before else "unknown" if mine else "no_answer"
            record = _observed(agent, session, call_id, event, last, order)
            record |= {"key": f"observed:{call_id}:{event}", "ts": began, "locked": bool(locked)}
            keep(agent, session, record)
    except Exception as error:
        note(agent, error, "observing")


def _earlier(first, second):
    """Whether one arrival is known to be before another: both timed, and differently."""
    timed = isinstance(first, int | float) and isinstance(second, int | float)
    return timed and first < second


def _sure(report, place, answer, at, earlier):
    """Whether an answer at place `at` is known to have come before (earlier) or after the
    report at `place`: its place and its time both say so, the times being different.

    A record's time is when its writer arrived and its place is where its append landed.
    Neither alone is the order: without the lock an append can land anywhere, and with it
    a writer that arrived first can still be the one that waited. So the lock is no
    shortcut here. Where the two disagree, or the times are equal, the order is not known.
    """
    when, then = report.get("ts"), answer.get("ts")
    if earlier:
        return at < place and _earlier(then, when)
    return at > place and _earlier(when, then)


def joined(records):
    """Each report of a call with the gate's answer it belongs to, worked out from what is
    on record and not from what a record says of itself:

        [{"agent", "session", "call_id", "reported", "order", "answered", "attempt", "ts"}]

    A call is an agent's, a session's and a call id together: two agents can use the same
    session and call ids, and an answer of one is never an answer for the other.

    For a report and an answer of its call, which came first is known in one way only:
    their places and their times agree, the times being different. An answer before the
    report in the file and dated earlier came first; one after it and dated later came
    after. Equal times say nothing, and neither does a time that contradicts the place,
    whether or not the records were written under the lock: a record's place is where its
    append landed and its time is when its writer arrived, and a report that arrived
    first can land second.

    order is "after_answer" when an answer is known to have come before the report,
    "before_answer" when every answer is known to have come after it, "no_answer" when
    none is on record for that call, and "unknown" otherwise. Nothing is made of a report
    whose order is unknown: it neither ran after a deny nor before the answer.
    """

    def call_of(record):
        return (record.get("agent"), record.get("session"), record.get("call_id"))

    answers, reports = {}, []
    for place, record in enumerate(records):
        if record.get("stage") == "answered":
            answers.setdefault(call_of(record), []).append((place, record))
        elif record.get("stage") == "observed" and not _tie(record):
            reports.append((place, record))  # a report itself, not a copy that ties one
    found = []
    for place, report in reports:
        mine = answers.get(call_of(report), [])
        when = report.get("ts")
        earlier = [answer for at, answer in mine if _sure(report, place, answer, at, True)]
        later = [answer for at, answer in mine if _sure(report, place, answer, at, False)]
        if earlier:
            order, answer = "after_answer", earlier[-1]
        elif mine and len(later) == len(mine):
            order, answer = "before_answer", later[0]
        elif mine:
            order, answer = "unknown", None
        else:
            order, answer = "no_answer", None
        told = {name: report.get(name) for name in ("agent", "session", "call_id", "reported")}
        told |= {"ts": when, "order": order, "answered": (answer or {}).get("answered")}
        found.append(told | {"attempt": (answer or {}).get("attempt")})
    return found


def _tie(record):
    """Whether an observed record is a copy that ties an earlier report to an answer."""
    return bool(record.get("tie")) or record.get("order") == "before_answer"


def reconcile(agent, session, call_id, answer):
    """Tie an answer to the reports of its call that are on record without one. Never raises.

    A result can be reported while the gate still waits for the server: the client did
    not wait, or ended the hook and went on. Such a report was recorded with no answer
    beside it. Now that there is one, each is recorded again with this answer and its
    attempt. One that arrived before the answer was made is "before_answer", and a result
    among them is "ran_before_answer": whatever the gate then answered came too late for
    that call. One that arrived at the same instant or after the answer was made, and
    only got its place first, is "unknown": nothing is made of it. Each report is tied once.
    """
    try:
        found = read(session_dir(agent, session))
        mine = [r for r in found if r.get("stage") == "observed" and r.get("call_id") == call_id]
        tied = {r.get("reported") for r in mine if _tie(r)}
        for report in mine:
            event = report.get("reported")
            if report.get("order") != "no_answer" or _tie(report) or event in tied:
                continue
            tied.add(event)
            first = _earlier(report.get("ts"), answer.get("ts"))
            order = "before_answer" if first else "unknown"
            record = _observed(agent, session, call_id, event, answer, order)
            record |= {"key": f"observed:{call_id}:{event}:before", "reported_ts": report.get("ts")}
            keep(agent, session, record | {"tie": True})
    except Exception as error:
        note(agent, error, "observing")


def examine(agent, raw, prepare, on_error):
    """(verdict, why, payload, receipt, question) for one hook event.

    verdict is "observed" for an event that needs no check, else "abstain", "deny" or
    "undecided". prepare() gives the way to the local server and may fail: a gate that cannot
    start has checked nothing, so that is handled like any other check that was not completed.
    receipt is the server's, or None when it gave none for this asking; question is what was
    asked, or None when it never came to asking.
    """
    event, payload = read_event(raw)
    recorded = True
    try:
        handle(agent, raw)  # recorded first, whatever happens next
    except Exception as error:
        recorded = False
        note(agent, error, "recording")
    if event is not None and event != "PreToolUse":
        if event in REPORTS:  # what became of a call a gate answered about
            observe(agent, payload, event)
        return "observed", None, payload, None, None  # every other event is only observed
    if payload is None:
        return "undecided", "event", None, None, None
    if not recorded:
        return "undecided", "recording", payload, None, None
    session, call_id = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session or not isinstance(call_id, str) or not call_id:
        return "undecided", "event", payload, None, None
    try:
        server = prepare()
    except Exception as error:
        note(agent, error, "setup")
        return "undecided", "setup", payload, None, None
    # The call is named by the hash of what the gate holds. The server reads the call from
    # its own log of the session and decides only about one with this hash.
    identity = call_sha256(payload.get("tool_name"), payload.get("tool_input"))
    asked = question(agent, session, call_id, identity, on_error)
    try:
        receipt = server.decide(asked)
    except Exception as error:  # no answer is not a pass
        note(agent, error, "server")
        return "undecided", "server", payload, None, asked
    if not bound_to(receipt, asked):  # an answer to another asking is no answer to this one
        return "undecided", "server", payload, None, asked
    return receipt["verdict"], receipt.get("why"), payload, receipt, asked


def check(agent, raw, prepare, on_error="deny", shadow=False):
    """Handle one hook event; returns (what to print or None, exit status).

    Nothing leaves this function except its answer: a failure anywhere, in reading, recording,
    starting or asking, is a check that was not completed. In shadow the answer is always
    nothing, and what it would have been is on record.
    """
    began = time.monotonic()
    try:
        verdict, why, payload, receipt, asked = examine(agent, raw, prepare, on_error)
    except Exception as error:
        note(agent, error, "failed")
        verdict, why, payload, receipt, asked = "undecided", "failed", None, None, None
    if verdict == "observed":
        return None, 0
    would = verdict == "deny" or (verdict == "undecided" and on_error != "abstain")
    # Shadow is this gate's own setting, or the server's word about its decisions. Either
    # is enough: a gate never enforces what its server issued as a shadow decision.
    shadow = bool(shadow) or (receipt or {}).get("mode") == "shadow"
    denied = would and not shadow
    if payload is not None:
        # What this gate answers about the call, whether or not a receipt came: one such
        # record for every call it handles, and the only one that says what was answered.
        # Keeping it is best effort. Whatever goes wrong with it, the answer below stands.
        try:
            answered(
                agent, payload, asked, verdict, why, denied, on_error, receipt, would, shadow, began
            )
        except Exception as error:
            note(agent, error, "keeping")
    if not denied:
        return None, 0
    if verdict == "deny":
        odds = (receipt or {}).get("p_deny")
        told = f"p_deny {odds:.2f}, " if isinstance(odds, float) else ""
        reason = f"{DENIED} ({told}unqualified model)."
    else:
        reason = f"{UNDECIDED} ({WHY.get(why, WHY['failed'])}); this action is denied."
    return denial("PreToolUse", reason)


def run(agent, raw, server, on_error="deny", shadow=False):
    """check() for a caller that already holds the way to the local server: local.Local for
    one that listens on a port, or the server's own Decider when it is in this process."""
    return check(agent, raw, lambda: server, on_error, shadow)


def main(args, prepare):
    """The hook itself: reads the event, prints a denial if there is one, returns the status.

    A denial goes to stdout in the agent's own shape and its reason to stderr as well, since an
    agent may read either when a hook exits with 2.
    """
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    except Exception as error:
        note(args.agent, error, "event")
        raw = None  # read_event treats it as an event that could not be read
    output, status = check(args.agent, raw, prepare, args.on_error, getattr(args, "shadow", False))
    if output is not None:
        print(json.dumps(output, allow_nan=False), flush=True)
        said = output["hookSpecificOutput"]
        print(said.get("permissionDecisionReason", ""), file=sys.stderr, flush=True)
    return status


__all__ = ["answered", "check", "denial", "keep", "main", "note", "observe", "outcome", "read"]
__all__ += ["joined", "one_at_a_time", "reconcile", "run"]
