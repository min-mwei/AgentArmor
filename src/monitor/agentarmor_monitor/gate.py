"""The gate: one hook for every event, which lets Kai decide about a suspicious tool call.

Every event is first recorded through Summarizer's hook, so nothing an agent reads or runs is
missed. For a tool call that is about to run (PreToolUse) the gate then waits for Summarizer's
summary of the session up to that call and looks at how the call was marked:

- not marked at the trigger level, in a session Summarizer scanned whole: the gate says
  nothing, and the agent's own permission flow applies;
- marked: the call goes to Kai with the rules and Summarizer's packet. Kai's "deny" is
  returned to the agent as a denial with the reason. Kai's "allow" says nothing, provided Kai
  was shown complete evidence from before the call (see below).

A check the gate cannot stand behind is not a pass. That covers an event it cannot read or
record, a gate that cannot start (no policy file, a bad Kai address), no summary of this exact
call in time, gaps in what Summarizer captured, a call too large to show Kai whole, an "allow"
Kai gave on a packet that left key evidence out or already held later events, and Kai being
unreachable or refusing the request. Each is denied with its reason, unless --on-error abstain
was chosen. Every such outcome is appended to the session's detections, so default denials can
be counted.

Kai's serving path is unqualified (policy_ready false): this gate applies an unqualified
model's decisions and is not a claim of attack prevention.
"""

import json
import os
import sys
import time

from summarizer.hook import MAX_STDIN, call_sha256, handle
from summarizer.store import AGENTS, home, load_summary, private_dir, safe_id, session_dir

from .escalate import LEVELS, SCHEMA, append, detect, read

DENIED = "AgentArmor: Kai denied this call under the session's rules"
UNDECIDED = "AgentArmor could not complete the check"
WHY = {
    "event": "the hook event could not be read",
    "recording": "the event could not be recorded",
    "setup": "the gate could not start",
    "no_summary": "no summary of this exact call arrived in time",
    "coverage": "the session record has gaps",
    "incomplete": "Kai was shown incomplete evidence",
    "moved_on": "the session had moved on when the call was checked",
    "call_too_large": "the call is too large to show Kai whole",
    "policy_too_large": "the rules leave no room for the session",
    "kai_unavailable": "Kai did not answer",
    "kai_rejected": "Kai refused the request",
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


def decide(agent, payload, client, level="medium", max_chars=5000, wait=3.0, rules=None):
    """(verdict, record, why) for a PreToolUse payload.

    verdict is "abstain", "deny" or "undecided". record is the detection when Kai was asked,
    else None. why names, for "undecided", what kept the check from being completed (a key of
    WHY); the caller applies its error policy.
    """
    session, call_id = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not isinstance(call_id, str) or not call_id:
        return "undecided", None, "event"
    identity = call_sha256(payload.get("tool_name"), payload.get("tool_input"))
    summary = load_summary(agent, safe_id(session), wait, call_id, identity)
    if summary is None or not summary.get("fresh") or summary.get("call") != "verified":
        return "undecided", None, "no_summary"  # nothing about this exact call to decide on
    entry = next(
        e for e in summary["entries"] if e.get("call_id") == call_id and e["type"] == "action"
    )
    if LEVELS.get(entry.get("level"), 0) < LEVELS[level]:
        # "Not marked" is a finding only when Summarizer scanned everything it captured.
        whole = summary["session"]["coverage"]["scanned_all_captured"]
        return ("abstain", None, None) if whole else ("undecided", None, "coverage")
    record = detect(client, summary, entry, max_chars, rules, payload.get("tool_input"))
    if record["status"] != "scored":
        return "undecided", record, record["status"]
    if record["decision"] == "deny":
        return "deny", record, None
    if record["decision"] != "allow":
        return "undecided", record, "failed"
    # An allow counts only on complete evidence from before the call.
    if not record["packet"]["complete"]:
        return "undecided", record, "incomplete"
    if record["review"] != "pre_action":
        return "undecided", record, "moved_on"
    return "abstain", record, None


def keep(agent, payload, record, verdict, why, enforced):
    """Append the gate's outcome for a call to its session's detections. Best effort."""
    session, call_id = payload.get("session_id"), payload.get("tool_use_id")
    if not isinstance(session, str) or not session:
        return
    if record is None:  # Kai was never asked: a record of the outcome alone
        record = {"schema": SCHEMA, "ts": round(time.time(), 3)}
        record |= {"key": f"gate:{call_id if isinstance(call_id, str) else ''}"}
        record |= {"agent": agent, "session": session, "status": why, "kai": None}
        record |= {"advisory": True, "policy_ready": False, "decision": None, "flagged": None}
    record |= {"gate": True, "verdict": verdict, "enforced": enforced}
    if why:
        record["why"] = why
    try:
        directory = session_dir(agent, session)
        private_dir(directory)
        append(directory, record)
    except Exception as error:
        note(agent, error, "keeping")


def examine(agent, raw, prepare):
    """(verdict, record, why, payload) for one hook event.

    verdict is "observed" for an event that needs no check, else "abstain", "deny" or
    "undecided". prepare() gives (client, options for decide) and may fail: a gate that cannot
    start has checked nothing, so that is handled like any other check that was not completed.
    """
    event, payload = read_event(raw)
    recorded = True
    try:
        handle(agent, raw)  # recorded first, whatever happens next
    except Exception as error:
        recorded = False
        note(agent, error, "recording")
    if event is not None and event != "PreToolUse":
        return "observed", None, None, payload  # every other event is only observed
    if payload is None:
        return "undecided", None, "event", None
    if not recorded:
        return "undecided", None, "recording", payload
    try:
        client, options = prepare()
    except Exception as error:
        note(agent, error, "setup")
        return "undecided", None, "setup", payload
    try:
        return (*decide(agent, payload, client, **options), payload)
    except Exception as error:  # a failed check is not a pass
        note(agent, error, "failed")
        return "undecided", None, "failed", payload


def check(agent, raw, prepare, on_error="deny"):
    """Handle one hook event; returns (what to print or None, exit status).

    Nothing leaves this function except its answer: a failure anywhere, in reading, recording,
    starting or deciding, is a check that was not completed.
    """
    try:
        verdict, record, why, payload = examine(agent, raw, prepare)
    except Exception as error:
        note(agent, error, "failed")
        verdict, record, why, payload = "undecided", None, "failed", None
    if verdict == "observed":
        return None, 0
    denied = verdict == "deny" or (verdict == "undecided" and on_error != "abstain")
    if payload is not None and (record is not None or verdict != "abstain"):
        keep(agent, payload, record, verdict, why, "deny" if denied else "none")
    if not denied:
        return None, 0
    if verdict == "deny":
        reason = f"{DENIED} (p_deny {record['kai']['p_deny']:.2f}, unqualified model)."
    else:
        reason = f"{UNDECIDED} ({WHY[why]}); this action is denied."
    return denial("PreToolUse", reason)


def run(agent, raw, client, on_error="deny", **options):
    """check() for a caller that already holds a Kai client and its options."""
    return check(agent, raw, lambda: (client, options), on_error)


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


__all__ = ["check", "decide", "denial", "main", "read", "run"]
