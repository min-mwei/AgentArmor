"""What earlier sessions hold for a proposed call, counted in shadow.

The gate's packet for the decision model is the call's own session. This looks, beside it
and for nobody's decision, at what Summarizer's lookup (summarizer.history) would add from
the other sessions on this machine, and says how that went as counts: how the call would
be routed, whether the history would be complete and why not, how much was found, how much
found no room, how long it took. Nothing of a session's text is returned or recorded.

It is a measurement of the lookup on real sessions, wanted before any route policy or
budget is fixed (the Model Reviewer's MR146). It changes no verdict, no request and no
receipt field that a gate acts on, and a failure here is a count of one failure.

Limits, said plainly. The other sessions are taken as their summaries stand now, with the
entries logged after the call was proposed left out by their time: an entry's own fields
(a call's status, say) can still say what was learned later. No session's namespace is
known here, so every source is searched as "unresolved". Only the newest sessions are
looked at, and how many were not is counted.
"""

import hashlib
import time
from pathlib import Path

from summarizer.store import list_sessions, load_summary

from summarizer import history

NEWEST = 40  # other sessions looked at, the most recently written first


def alias(agent, name):
    return hashlib.sha256(f"{agent}/{name}".encode()).hexdigest()[:4]


def observed(agent, name, call_id, summary, max_chars, identity=None, newest=NEWEST):
    """Counts of what the lookup finds for one call, or {"error": kind} when it fails."""
    began = time.monotonic()
    try:
        call = next(
            e for e in summary["entries"] if e.get("call_id") == call_id and e["type"] == "action"
        )
        cut = float(call.get("ts") or 0.0)

        def known(a, n, held):
            fields = {"key": f"{a}/{n}", "summary": held, "namespace": None}
            fields |= {"cwd": str(held["session"].get("cwd") or ""), "alias": alias(a, n)}
            return fields | {"agent": a, "session_id": n}

        listed = [(a, n) for a, n, _, _ in list_sessions() if (a, n) != (agent, name)]
        others = []
        for a, n in listed[:newest]:
            held = load_summary(a, n)
            if held is None:
                continue
            before = [e for e in held["entries"] if float(e.get("ts") or 0.0) <= cut]
            if before:
                first = min(float(e.get("ts") or 0.0) for e in before)
                others.append((first, known(a, n, held | {"entries": before})))
        others = [other for _, other in sorted(others, key=lambda pair: pair[0])]
        view = history.packet(
            known(agent, name, summary), call_id, others, str(Path.home()), max_chars, identity
        )
        account = view.get("history") or {}
        told = {"route": view.get("route"), "route_reason": view.get("route_reason")}
        told |= {"complete": account.get("complete", True), "why": account.get("why", [])}
        told |= {"sources": len(account.get("sources", ())), "lines": len(account.get("items", ()))}
        told |= {"bridges": len(account.get("bridges", ()))}
        told |= {"left_out": account.get("left_out", {})}
        told |= {"unsupported": account.get("unsupported", [])}
        told |= {"controls_not_applying": len(account.get("controls_not_applying", ()))}
        told |= {"sessions_looked_at": len(others), "sessions_not_looked_at": len(listed[newest:])}
        told |= {"chars": len(view["text"]), "max_chars": max_chars}
    except Exception as error:  # a measurement beside a decision never stops the decision
        told = {"error": type(error).__name__}
    return told | {"ms": round((time.monotonic() - began) * 1000)}
