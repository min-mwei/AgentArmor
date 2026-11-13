"""What earlier sessions hold for a proposed call, counted in shadow and off the decision's path.

The gate's packet for the decision model is the call's own session. This looks, beside it
and for nobody's decision, at what Summarizer's lookup (summarizer.history) would add from
the other sessions on this machine, and says how that went as counts: how the call would
be routed, whether the lookup's own history would be complete and why not, how much was
found, how much found no room, how long it took, and how much of the other sessions it
could read at all. Nothing of a session's text is returned or recorded.

It is a measurement of the lookup on real sessions, wanted before any route policy or
budget is fixed (the Model Reviewer's MR146). A decision never waits for it (MR159-1): a
decision hands over what it had read and goes on; one worker of its own does the counting,
from a queue of fixed length, and writes a record of its own beside the session's
detections. A queue that is full drops the offer and counts it. Its work holds no
decision slot and reaches no verdict, request or receipt.

Every validated question a gate asks is counted once, at the decider's door, with the
attempt it carries (MR160-1): it is offered to the worker, or dropped because the queue is
full, or not measured, with why (busy, failed, no summary, a summary made otherwise). Each
disposition is a record of its own keyed by the attempt, so that a retry is never taken
for another call and the counts reconcile: attempts = offered + not measured, offered =
queued + dropped, queued = measured + failed + in flight.

What the counts cover is said with them (MR159-2). Every other session listed is in
exactly one of: read, no summary or an unreadable one, a summary behind its log, nothing
logged before the call, or beyond the cap. "complete" is the lookup's own account of the
summaries it was given. "coverage_known" is whether every listed session could be read
up to date: where it is false, nothing here says that earlier sessions hold nothing.

LIMITS is recorded with every count, because the instrument is not a replay: the other
sessions are taken as their summaries stand when the worker runs, with entries logged
after the call left out by their time; no session's namespace is known; only the newest
sessions are looked at.
"""

import hashlib
import json
import queue
import threading
import time
from pathlib import Path

from summarizer.store import list_sessions, load_summary, private_dir, session_dir

from summarizer import history

SCHEMA = "agentarmor-earlier-sessions/v1"
RECORDS = "earlier-sessions.jsonl"  # beside a session's detections; counts only
NEWEST = 40  # other sessions looked at, the most recently written first
WAITING = 32  # offers that can wait for the worker; one more is dropped and counted
LIMITS = {
    "other_sessions": "latest_summary_cut_at_the_calls_time",  # not a replay to the call
    "namespaces": "unknown",
    "newest_sessions_only": NEWEST,
}


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
        sessions = {"listed": len(listed), "read": 0, "no_summary_or_unreadable": 0}
        sessions |= {"summary_behind_its_log": 0, "nothing_before_the_call": 0}
        sessions["beyond_the_cap"] = len(listed[newest:])
        others = []
        for a, n in listed[:newest]:
            try:
                held = load_summary(a, n)
                entries = held["entries"] if held is not None else None
            except Exception:
                entries = None
            if entries is None:
                sessions["no_summary_or_unreadable"] += 1
                continue
            before = [e for e in entries if float(e.get("ts") or 0.0) <= cut]
            if not held.get("fresh"):
                # What its log holds beyond the summary is not known, before the call or
                # after: it is searched as far as it goes and counted as behind.
                sessions["summary_behind_its_log"] += 1
            elif not before:
                sessions["nothing_before_the_call"] += 1
            else:
                sessions["read"] += 1
            if before:
                first = min(float(e.get("ts") or 0.0) for e in before)
                others.append((first, known(a, n, held | {"entries": before})))
        others = [other for _, other in sorted(others, key=lambda pair: pair[0])]
        view = history.packet(
            known(agent, name, summary), call_id, others, str(Path.home()), max_chars, identity
        )
        account = view.get("history") or {}
        unread = sessions["no_summary_or_unreadable"] + sessions["summary_behind_its_log"]
        told = {"route": view.get("route"), "route_reason": view.get("route_reason")}
        told |= {"complete": account.get("complete", True), "why": account.get("why", [])}
        told |= {"coverage_known": unread + sessions["beyond_the_cap"] == 0}
        told |= {"sources": len(account.get("sources", ())), "lines": len(account.get("items", ()))}
        told |= {"bridges": len(account.get("bridges", ()))}
        told |= {"left_out": account.get("left_out", {})}
        told |= {"unsupported": account.get("unsupported", [])}
        told |= {"controls_not_applying": len(account.get("controls_not_applying", ()))}
        told |= {"sessions": sessions, "chars": len(view["text"]), "max_chars": max_chars}
    except Exception as error:  # a measurement never raises into whoever asked for it
        told = {"error": type(error).__name__}
    return told | {"limits": LIMITS, "ms": round((time.monotonic() - began) * 1000)}


def _write(agent, name, record):
    """Append one record beside a session's detections. Best effort, and never raises."""
    try:
        directory = session_dir(agent, name)
        private_dir(directory)
        with open(directory / RECORDS, "a", encoding="utf-8") as out:
            out.write(json.dumps(record, separators=(",", ":")) + "\n")
        return True
    except Exception:
        return False


class Ticket:
    """One attempt's way to the measurement: offered once, or closed with why it was not."""

    def __init__(self, shadow, asked):
        self.shadow, self.asked, self.done = shadow, asked, False

    def offer(self, name, summary):
        if not self.done:
            self.done = True
            self.shadow._offer(self.asked, name, summary)

    def close(self, why):
        """At the end of a decision, whatever became of it: counted unless already offered."""
        if not self.done:
            self.done = True
            self.shadow._unmeasured(self.asked, why or "unknown")


class Shadow:
    """The worker that counts, and the queue a decision hands its call to without waiting."""

    def __init__(self, max_chars, waiting=WAITING, newest=NEWEST):
        self.max_chars, self.newest = max_chars, newest
        self.offers = queue.Queue(maxsize=waiting)
        self.counts = {"attempts": 0, "offered": 0, "dropped_queue_full": 0}
        self.counts |= {"measured": 0, "failed": 0, "records_not_written": 0}
        self.counts["not_measured"] = {}  # why an attempt never reached the lookup
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._work, name="earlier-sessions", daemon=True)
        self.worker.start()

    def _count(self, name, why=None):
        with self.lock:
            if why is None:
                self.counts[name] += 1
            else:
                self.counts[name][why] = self.counts[name].get(why, 0) + 1

    def ticket(self, asked):
        """A ticket for one validated question; every one ends offered or not measured."""
        self._count("attempts")
        return Ticket(self, asked)

    def _base(self, asked, disposition):
        record = {"schema": SCHEMA, "ts": round(time.time(), 3), "agent": asked["agent"]}
        record |= {"session": asked["session_id"], "key": f"gate:{asked['tool_use_id']}"}
        record |= {"call_sha256": asked["call_sha256"], "attempt": asked["attempt"]}
        return record | {"disposition": disposition}

    def _offer(self, asked, name, summary):
        """Hand a call over. Never waits: a full queue drops the offer and counts it."""
        self._count("offered")
        try:
            self.offers.put_nowait((asked, name, summary))
        except queue.Full:
            self._count("dropped_queue_full")
            if not _write(asked["agent"], name, self._base(asked, "dropped_queue_full")):
                self._count("records_not_written")

    def _unmeasured(self, asked, why):
        """An attempt that never reached the lookup: counted, and kept with its reason, so
        that it is not taken for one about which nothing was found."""
        self._count("not_measured", why)
        record = self._base(asked, "not_measured") | {"why": why}
        if not _write(asked["agent"], asked["session_id"], record):
            self._count("records_not_written")

    def _work(self):
        while True:
            offered = self.offers.get()
            if offered is None:
                return
            asked, name, summary = offered
            try:
                told = observed(
                    asked["agent"],
                    name,
                    asked["tool_use_id"],
                    summary,
                    self.max_chars,
                    asked["call_sha256"],
                    self.newest,
                )
                self._count("failed" if "error" in told else "measured")
                done = "failed" if "error" in told else "measured"
                if not _write(asked["agent"], name, self._base(asked, done) | told):
                    self._count("records_not_written")
            finally:
                self.offers.task_done()

    def about(self):
        with self.lock:
            told = json.loads(json.dumps(self.counts))
        return told | {"in_flight": self.offers.unfinished_tasks, "queue": self.offers.maxsize}

    def drain(self, timeout=10.0):
        """Wait until what was offered has been counted. For tests and for an orderly stop."""
        deadline = time.monotonic() + timeout
        while self.offers.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self.offers.unfinished_tasks

    def close(self):
        self.offers.put(None)
