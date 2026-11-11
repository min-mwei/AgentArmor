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

The decision model's serving path is unqualified (policy_ready false): this gate applies an
unqualified model's decisions and is not a claim of attack prevention.
"""

import json
import os
import sys
import time

from summarizer.hook import MAX_STDIN, call_sha256, handle
from summarizer.store import AGENTS, home, private_dir, session_dir

from .escalate import SCHEMA, append, provenance, read
from .local import bound_to, question

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
    "call_too_large": "the call is too large to show Kai whole",
    "policy_too_large": "the rules leave no room for the session",
    "kai_unavailable": "Kai did not answer",
    "kai_rejected": "Kai refused the request",
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


def keep(agent, payload, record, verdict, why, enforced):
    """Append the gate's outcome for a call to its session's detections. Best effort."""
    session, call_id = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session:
        return
    if record is None:  # Kai was never asked, and no summary was read: the outcome alone
        record = outcome(agent, session, call_id, why)
    record |= {"gate": True, "verdict": verdict, "enforced": enforced}
    if why:
        record["why"] = why
    try:
        directory = session_dir(agent, session)
        private_dir(directory)
        append(directory, record)
    except Exception as error:
        note(agent, error, "keeping")


def examine(agent, raw, prepare, on_error):
    """(verdict, why, payload, receipt) for one hook event.

    verdict is "observed" for an event that needs no check, else "abstain", "deny" or
    "undecided". prepare() gives the way to the local server and may fail: a gate that cannot
    start has checked nothing, so that is handled like any other check that was not completed.
    receipt is the server's, or None when it gave none for this call.
    """
    event, payload = read_event(raw)
    recorded = True
    try:
        handle(agent, raw)  # recorded first, whatever happens next
    except Exception as error:
        recorded = False
        note(agent, error, "recording")
    if event is not None and event != "PreToolUse":
        return "observed", None, payload, None  # every other event is only observed
    if payload is None:
        return "undecided", "event", None, None
    if not recorded:
        return "undecided", "recording", payload, None
    session, call_id = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session or not isinstance(call_id, str) or not call_id:
        return "undecided", "event", payload, None
    try:
        server = prepare()
    except Exception as error:
        note(agent, error, "setup")
        return "undecided", "setup", payload, None
    # The call is named by the hash of what the gate holds. The server reads the call from
    # its own log of the session and decides only about one with this hash.
    identity = call_sha256(payload.get("tool_name"), payload.get("tool_input"))
    asked = question(agent, session, call_id, identity, on_error)
    try:
        receipt = server.decide(asked)
    except Exception as error:  # no answer is not a pass
        note(agent, error, "server")
        return "undecided", "server", payload, None
    if not bound_to(receipt, asked):  # an answer about something else is no answer
        return "undecided", "server", payload, None
    return receipt["verdict"], receipt.get("why"), payload, receipt


def check(agent, raw, prepare, on_error="deny"):
    """Handle one hook event; returns (what to print or None, exit status).

    Nothing leaves this function except its answer: a failure anywhere, in reading, recording,
    starting or asking, is a check that was not completed.
    """
    try:
        verdict, why, payload, receipt = examine(agent, raw, prepare, on_error)
    except Exception as error:
        note(agent, error, "failed")
        verdict, why, payload, receipt = "undecided", "failed", None, None
    if verdict == "observed":
        return None, 0
    denied = verdict == "deny" or (verdict == "undecided" and on_error != "abstain")
    if receipt is None and payload is not None:
        # The server kept nothing about this outcome, so the gate does: it stays countable.
        keep(agent, payload, None, verdict, why, "deny" if denied else "none")
    if not denied:
        return None, 0
    if verdict == "deny":
        odds = receipt.get("p_deny")
        told = f"p_deny {odds:.2f}, " if isinstance(odds, float) else ""
        reason = f"{DENIED} ({told}unqualified model)."
    else:
        reason = f"{UNDECIDED} ({WHY.get(why, WHY['failed'])}); this action is denied."
    return denial("PreToolUse", reason)


def run(agent, raw, server, on_error="deny"):
    """check() for a caller that already holds the way to the local server: local.Local for
    one that listens on a port, or the server's own Decider when it is in this process."""
    return check(agent, raw, lambda: server, on_error)


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
    output, status = check(args.agent, raw, prepare, args.on_error)
    if output is not None:
        print(json.dumps(output, allow_nan=False), flush=True)
        said = output["hookSpecificOutput"]
        print(said.get("permissionDecisionReason", ""), file=sys.stderr, flush=True)
    return status


__all__ = ["check", "denial", "keep", "main", "note", "outcome", "read", "run"]
