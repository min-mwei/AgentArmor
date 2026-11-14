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
for another call. A decision does no file work at all (MR162-1): records go to a writer of
their own through a bounded queue, and one that finds it full is counted as lost. The
counts reconcile at every moment, phase by phase (Shadow, MR162-2).

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
import os
import queue
import threading
import time
from pathlib import Path

from summarizer.index import Index
from summarizer.store import AGENTS, EVENTS, home, load_summary, private_dir, session_dir

from summarizer import history

SCHEMA = "agentarmor-earlier-sessions/v1"
RECORDS = "earlier-sessions.jsonl"  # beside a session's detections; counts only
NEWEST = 40  # other sessions looked at, the most recently written first
INDEX_WORK = 2_000_000  # SQLite steps the proof of what is apart may take on a decision's path
WAITING = 32  # offers that can wait for the worker; one more is dropped and counted
RECORDS_WAITING = 256  # records that can wait for the writer; one more is lost and counted
LIMITS = {
    "other_sessions": "latest_summary_cut_at_the_calls_time",  # not a replay to the call
    "namespaces": "unknown",
    "newest_sessions_only": NEWEST,
}


def alias(agent, name):
    return hashlib.sha256(f"{agent}/{name}".encode()).hexdigest()[:4]


def known(agent, name, held):
    """A session as the lookup takes it (summarizer.history.packet): no namespace is known."""
    fields = {"key": f"{agent}/{name}", "summary": held, "namespace": None}
    fields |= {"cwd": str(held["session"].get("cwd") or ""), "alias": alias(agent, name)}
    return fields | {"agent": agent, "session_id": name}


def _listed(agent, name):
    """(agent, name, its log's size, its log's mtime) of every other spooled session, newest first.

    One stat for each session, which listing them takes anyway: no log is opened here."""
    found = []
    for a in AGENTS:
        root = home() / "sessions" / a
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            if (a, entry.name) == (agent, name):
                continue
            try:
                stat = os.stat(os.path.join(entry.path, EVENTS))
            except OSError:
                continue
            found.append((a, entry.name, stat.st_size, stat.st_mtime))
    return sorted(found, key=lambda item: -item[3])


SHELL_TOOLS = ("Bash", "exec_command", "shell", "local_shell")


def _proven_apart(listed, here, call_uses, cut, home_dir, work=None):
    """(the sessions the search index proves cannot bear on the call, how the proof went) (MR171-1,
    MR173). How it went is "used", or why nothing was proven: "no_index", "index_unreadable",
    "call_uses_not_fully_known" or "index_work_exhausted".

    A session is set aside only when all of this holds: the index covers its whole log as it is
    now; it ran in another working directory (the user's words in the call's own project bear on
    the call, whatever they name); and nothing it logged before the call could reach what the
    call uses. That last is decided conservatively over the index's pieces, which hold a file
    tool's target and every piece's text: a session is not set aside if any piece targets a file
    the call runs, reads, writes or names, or a path under the call's working directory or among
    the home directory's hidden files; or if any piece's text mentions the call's working
    directory, the name of a file the call uses, a hidden path of the home directory or "~/"; or
    if it ran a shell command with a variable or a "../" in it, whose files cannot be told. The
    call's own uses must be fully known: a pattern, a module found by name or an unsupported
    command proves nothing. Without an index, or when it cannot be read, nothing is proven and
    every session not read is unknown.

    A piece logged at the very time of the call counts as before it (MR173-1): the hook keeps
    times to the millisecond, and a tie proves nothing about which came first. The query runs
    under a budget of SQLite steps (INDEX_WORK): when it runs out, nothing is proven and every
    session not read stays unknown. No log is opened."""
    if call_uses["unsupported"] or call_uses["patterns"] or call_uses["by_name"] or not here:
        return {}, "call_uses_not_fully_known"
    exact = sorted(
        {*call_uses["run"], *call_uses["read"], *call_uses["named"]}
        | set(call_uses.get("writes") or ([call_uses["write"]] if call_uses["write"] else []))
    )
    bases = sorted({os.path.basename(p) for p in exact if len(os.path.basename(p)) >= 3})
    try:
        index = Index()
    except FileNotFoundError:
        return {}, "no_index"
    except Exception:
        return {}, "index_unreadable"

    def like(text, before="%", after="%"):
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return before + escaped + after

    hidden = home_dir.rstrip("/") + "/."
    tests = ["u.origin = ?"] * len(exact) + ["u.origin LIKE ? ESCAPE '\\'"] * 2
    given = [*exact, like(here.rstrip("/") + "/", ""), like(hidden, "")]
    for text in (here, hidden, "~/", *bases):
        tests.append("u.text LIKE ? ESCAPE '\\'")
        given.append(like(text))
    shells = ", ".join("?" * len(SHELL_TOOLS))
    tests.append(f"(u.tool IN ({shells}) AND (u.text LIKE '%$%' OR u.text LIKE '%../%'))")
    given.extend(SHELL_TOOLS)
    query = (
        "SELECT DISTINCT s.agent, s.name FROM units u JOIN sessions s ON s.id = u.session"
        " WHERE u.ts <= ? AND (" + " OR ".join(tests) + ")"
    )
    steps = {"left": INDEX_WORK if work is None else work}
    every = max(1, min(1000, steps["left"]))  # how many steps between two looks at the budget

    def spend():
        steps["left"] -= every
        return 1 if steps["left"] <= 0 else 0  # non-zero interrupts the statement

    try:
        index.db.set_progress_handler(spend, every)
        rows = {(r["agent"], r["name"]): r for r in index.sessions()}
        reach = {(r["agent"], r["name"]) for r in index.db.execute(query, [cut, *given]).fetchall()}
    except Exception:
        return {}, "index_work_exhausted" if steps["left"] <= 0 else "index_unreadable"
    finally:
        index.close()
    proven = {}
    for a, n, size, _ in listed:
        row = rows.get((a, n))
        if (
            row
            and row["log_bytes"] >= size
            and row["cwd"]
            and row["cwd"] != here
            and (a, n) not in reach
        ):
            proven[(a, n)] = "index_shows_nothing_it_could_reach"
    return proven, "used"


def scope(agent, name, summary, call_id, home_dir, budget=None):
    """(the other sessions as the lookup takes them, oldest first, each cut at the call's time;
    coverage counts). The one rule for both the routing of a call and whether its packet is
    complete, in the shadow count and in the "on" mode (MR169-1, MR171).

    Every other session is in exactly one of: read (its summary is up to date and searched);
    proven apart by the search index (_proven_apart); or unknown: no summary or one that cannot
    be read, a summary behind its log (searched as far as it goes, and still unknown beyond
    it), or beyond the work budget. Sessions not proven apart are taken newest first, at most
    `budget` summaries (NEWEST unless given): a decision opens no more than that, and never a
    log, and the index's proof runs under its own budget (_proven_apart). "unknown" is how many
    sessions might hold something that bears on the call and could not be read: coverage is
    known only when it is 0. A session with an entry at the very time of the call is read and
    its entry kept (it may bear on the call), and is counted unknown as well, "tied_with_the_call":
    which came first is not known, so it can never make a packet complete (MR173-1)."""
    budget = NEWEST if budget is None else budget
    call = next(
        e for e in summary["entries"] if e.get("call_id") == call_id and e["type"] == "action"
    )
    cut = float(call.get("ts") or 0.0)
    here = str(summary["session"].get("cwd") or "")
    listed = _listed(agent, name)
    proven, proof = _proven_apart(listed, here, history.uses(call, here, home_dir), cut, home_dir)
    open_to = [item for item in listed if (item[0], item[1]) not in proven]
    counts = {"listed": len(listed), "read": 0, "proven_apart": len(proven), "proof": proof}
    counts["tied_with_the_call"] = 0
    counts |= {"no_summary_or_unreadable": 0, "summary_behind_its_log": 0}
    counts |= {"nothing_before_the_call": 0, "beyond_the_budget": len(open_to[budget:])}
    others = []
    for a, n, _, _ in open_to[:budget]:
        try:
            held = load_summary(a, n)
            entries = held["entries"] if held is not None else None
        except Exception:
            entries = None
        if entries is None:
            counts["no_summary_or_unreadable"] += 1
            continue
        before = [e for e in entries if float(e.get("ts") or 0.0) <= cut]
        if any(float(e.get("ts") or 0.0) == cut for e in before):
            counts["tied_with_the_call"] += 1
        if not held.get("fresh"):
            # What its log holds beyond the summary is not known, before the call or
            # after: it is searched as far as it goes and counted as behind.
            counts["summary_behind_its_log"] += 1
        elif not before:
            counts["nothing_before_the_call"] += 1
        else:
            counts["read"] += 1
        if before:
            first = min(float(e.get("ts") or 0.0) for e in before)
            others.append((first, known(a, n, held | {"entries": before})))
    counts["unknown"] = (
        counts["no_summary_or_unreadable"]
        + counts["summary_behind_its_log"]
        + counts["beyond_the_budget"]
        + counts["tied_with_the_call"]
    )
    return [other for _, other in sorted(others, key=lambda pair: pair[0])], counts


def others_of(agent, name, summary, call_id, newest=NEWEST):
    """scope() with this machine's home directory: kept for the shadow count's callers."""
    return scope(agent, name, summary, call_id, str(Path.home()), newest)


def coverage_known(sessions):
    """Whether every other session that might bear on the call could be read up to date (MR171)."""
    return sessions.get("unknown", 1) == 0 and "error" not in sessions


def observed(agent, name, call_id, summary, max_chars, identity=None, newest=NEWEST):
    """Counts of what the lookup finds for one call, or {"error": kind} when it fails."""
    began = time.monotonic()
    try:
        others, sessions = others_of(agent, name, summary, call_id, newest)
        view = history.packet(
            known(agent, name, summary), call_id, others, str(Path.home()), max_chars, identity
        )
        account = view.get("history") or {}
        told = {"route": view.get("route"), "route_reason": view.get("route_reason")}
        told |= {"complete": account.get("complete", True), "why": account.get("why", [])}
        told |= {"coverage_known": coverage_known(sessions)}
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
    """The worker that counts, the writer that keeps its records, and the two queues a
    decision hands work to without waiting.

    A decision touches memory only (MR162-1): it counts, and puts a measurement or a
    record on a bounded queue with put_nowait. The worker measures; the writer appends the
    records. Either queue full drops what it was offered, and that is counted, never waited
    for. Every count moves between phases under one lock, so a snapshot at any moment
    reconciles (MR162-2):

        attempts = deciding + offered + not_measured
        offered  = dropped + queued + running + measured + failed
        records  = records_queued + records_written + records_lost + records_failed
    """

    def __init__(self, max_chars, waiting=WAITING, newest=NEWEST, records=RECORDS_WAITING):
        self.max_chars, self.newest = max_chars, newest
        self.offers = queue.Queue(maxsize=waiting)
        self.records = queue.Queue(maxsize=records)
        self.counts = {"attempts": 0, "deciding": 0, "offered": 0, "dropped_queue_full": 0}
        self.counts |= {"queued": 0, "running": 0, "measured": 0, "failed": 0, "not_measured": {}}
        self.counts |= {"records": 0, "records_queued": 0, "records_written": 0}
        self.counts |= {"records_lost": 0, "records_failed": 0}
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._work, name="earlier-sessions", daemon=True)
        self.writer = threading.Thread(target=self._keep, name="earlier-records", daemon=True)
        self.worker.start()
        self.writer.start()

    def ticket(self, asked):
        """A ticket for one validated question; every one ends offered or not measured."""
        with self.lock:
            self.counts["attempts"] += 1
            self.counts["deciding"] += 1
        return Ticket(self, asked)

    def _base(self, asked, disposition):
        record = {"schema": SCHEMA, "ts": round(time.time(), 3), "agent": asked["agent"]}
        record |= {"session": asked["session_id"], "key": f"gate:{asked['tool_use_id']}"}
        record |= {"call_sha256": asked["call_sha256"], "attempt": asked["attempt"]}
        return record | {"disposition": disposition}

    def _record(self, agent, name, record):
        """Hand a record to the writer. Never waits: called with the lock held."""
        self.counts["records"] += 1
        try:
            self.records.put_nowait((agent, name, record))
            self.counts["records_queued"] += 1
        except queue.Full:
            self.counts["records_lost"] += 1

    def _offer(self, asked, name, summary):
        """Hand a call over. Never waits: a full queue drops the offer and counts it."""
        with self.lock:
            self.counts["deciding"] -= 1
            self.counts["offered"] += 1
            try:
                self.offers.put_nowait((asked, name, summary))
                self.counts["queued"] += 1
            except queue.Full:
                self.counts["dropped_queue_full"] += 1
                self._record(asked["agent"], name, self._base(asked, "dropped_queue_full"))

    def _unmeasured(self, asked, why):
        """An attempt that never reached the lookup: counted, and kept with its reason, so
        that it is not taken for one about which nothing was found."""
        with self.lock:
            self.counts["deciding"] -= 1
            missed = self.counts["not_measured"]
            missed[why] = missed.get(why, 0) + 1
            record = self._base(asked, "not_measured") | {"why": why}
            self._record(asked["agent"], asked["session_id"], record)

    def _work(self):
        while True:
            offered = self.offers.get()
            if offered is None:
                return
            asked, name, summary = offered
            with self.lock:
                self.counts["queued"] -= 1
                self.counts["running"] += 1
            told = observed(
                asked["agent"],
                name,
                asked["tool_use_id"],
                summary,
                self.max_chars,
                asked["call_sha256"],
                self.newest,
            )
            done = "failed" if "error" in told else "measured"
            with self.lock:
                self.counts["running"] -= 1
                self.counts[done] += 1
                self._record(asked["agent"], name, self._base(asked, done) | told)

    def _keep(self):
        while True:
            held = self.records.get()
            if held is None:
                return
            written = _write(*held)
            with self.lock:
                self.counts["records_queued"] -= 1
                self.counts["records_written" if written else "records_failed"] += 1

    def about(self):
        """One snapshot of every count, taken under the lock: it reconciles as it stands."""
        with self.lock:
            told = json.loads(json.dumps(self.counts))
        return told | {"queue": self.offers.maxsize, "records_queue": self.records.maxsize}

    def drain(self, timeout=10.0):
        """Wait until nothing is deciding, queued, running or unwritten. For tests and stops."""
        deadline = time.monotonic() + timeout
        busy = ("deciding", "queued", "running", "records_queued")
        while time.monotonic() < deadline:
            with self.lock:
                if not any(self.counts[name] for name in busy):
                    return True
            time.sleep(0.01)
        return False

    def close(self):
        self.offers.put(None)
        self.records.put(None)
