"""The monitor daemon: Summarizer's daemon, plus escalation of what it flags to Kai.

Summarizer keeps its embedding model resident and every session's summary current from the
hooks' event logs. After each update, this daemon queues each new trigger for Kai with a copy
of the summary it was found in; a separate worker sends them and appends the results to the
session's detections.jsonl. Triggers queued from one summary that build the very same request
(several marked pieces of one result do) are put to Kai once, and each still gets its record.

The two never wait on each other. A summary update only queues work, so a slow or unreachable
Kai cannot hold back any session's summary, which a gate may be waiting for. The queue is
bounded: what does not fit stays pending and is picked up on a later pass. Kai being down never
stops monitoring: the first trigger that finds it down is put on record once, everything queued
is put back, and all of it is tried again later; the summary is written either way.
"""

import json
import queue
import threading
import time

from summarizer.daemon import Daemon
from summarizer.store import home, view

from .escalate import append, detect, key_of, read, settled, triggers


class Monitor(Daemon):
    def __init__(
        self,
        runtime,
        client,
        level="low",
        max_chars=5000,
        per_update=8,
        retry=30.0,
        backlog=64,
        **daemon,
    ):
        super().__init__(runtime, **daemon)
        self.client, self.level, self.max_chars = client, level, max_chars
        self.per_update, self.retry = per_update, retry
        self.rules = None  # the default rules unless set
        self.jobs = queue.Queue(backlog)  # (agent, name, directory, summary, entry, asked)
        self.state = threading.Lock()  # guards the four collections below
        self.done = {}  # (agent, name) -> keys that need no further attempt
        self.queued = set()  # (agent, name, key) waiting for the worker or with it
        self.waiting = {}  # (agent, name) -> when its pending triggers may be tried again
        self.reported = set()  # (agent, name, key) already recorded as waiting for Kai
        self.worker, self.stopping = None, threading.Event()

    def update(self, agent, name):
        consumed = super().update(agent, name)
        with self.state:
            due = (agent, name) in self.waiting
        if consumed or due:
            self.escalate(agent, name)
        return consumed

    def stale(self):
        now = time.monotonic()
        with self.state:
            due = {key for key, at in self.waiting.items() if at <= now}
        return super().stale() | due

    def escalate(self, agent, name):
        """Queue this session's new triggers for Kai; returns how many were queued.

        Each is queued with a copy of the summary it was found in, so what Kai is later shown
        is the session as it stood then, whatever has been logged since.
        """
        session = self.sessions.get((agent, name))
        if session is None:
            return 0
        directory = home() / "sessions" / agent / name
        with self.state:
            if (agent, name) not in self.done:
                self.done[(agent, name)] = settled(read(directory))
            skip = set(self.done[(agent, name)])
            skip |= {key for a, n, key in self.queued if (a, n) == (agent, name)}
            self.waiting.pop((agent, name), None)
        live = view(session.summary())
        pending = [key_of(e) for e in triggers(live, self.level) if key_of(e) not in skip]
        if not pending:
            return 0
        # A copy the session can no longer change: the worker reads it on another thread, later.
        summary = json.loads(json.dumps(live))
        entries = {key_of(entry): entry for entry in triggers(summary, self.level)}
        # Triggers queued together share this summary, so a request two of them build alike
        # is put to Kai once. Nothing is shared with a later pass: that is a later view.
        queued, asked = 0, {}
        for key in pending[: self.per_update]:
            with self.state:
                self.queued.add((agent, name, key))
            try:
                self.jobs.put_nowait((agent, name, directory, summary, entries[key], asked))
            except queue.Full:
                with self.state:
                    self.queued.discard((agent, name, key))
                break
            queued += 1
        if queued < len(pending):  # more to send, or no room: look again on the next pass
            with self.state:
                self.waiting.setdefault((agent, name), time.monotonic())
        self.start()
        return queued

    def start(self):
        """Make sure the worker that talks to Kai is running."""
        if self.worker is None or not self.worker.is_alive():
            self.stopping.clear()
            self.worker = threading.Thread(target=self._work, name="kai-escalation", daemon=True)
            self.worker.start()

    def _work(self):
        while not self.stopping.is_set():
            try:
                job = self.jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._send(*job)
            except Exception as error:  # one bad trigger must not stop the others
                self.log(f"escalation failed: {type(error).__name__}")
            finally:
                self.jobs.task_done()

    def _send(self, agent, name, directory, summary, entry, asked):
        key = key_of(entry)
        try:
            if key in settled(read(directory)):  # a gate asked about this call already
                with self.state:
                    self.done.setdefault((agent, name), set()).add(key)
                return
            record = detect(
                self.client,
                summary,
                entry,
                self.max_chars,
                self.rules,
                directory=directory,
                asked=asked,
            )
        finally:
            with self.state:
                self.queued.discard((agent, name, key))
        if record["status"] == "kai_unavailable":
            again = time.monotonic() + self.retry
            with self.state:
                self.waiting[(agent, name)] = again
                first = (agent, name, key) not in self.reported
                self.reported.add((agent, name, key))
            if first:  # said once, so the gap is on record
                append(directory, record)
            put_back = self._defer(again)
            self.log(f"{agent}/{name}: Kai did not answer; {put_back + 1} triggers wait")
            return
        with self.state:
            self.done.setdefault((agent, name), set()).add(key)
        append(directory, record)
        told = "flagged" if record.get("flagged") else record["status"]
        about = record["trigger"]
        self.log(f"{agent}/{name}: {about['type']} #{about['seq']} {told}")

    def _defer(self, again):
        """Kai is not answering: put back everything queued, to be tried from `again` on."""
        count = 0
        while True:
            try:
                agent, name, _, _, entry, _ = self.jobs.get_nowait()
            except queue.Empty:
                return count
            with self.state:
                self.queued.discard((agent, name, key_of(entry)))
                self.waiting[(agent, name)] = again
            self.jobs.task_done()
            count += 1

    def drain(self, timeout=10.0):
        """Wait until the worker has sent everything queued. True if it finished in time."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.state:
                idle = not self.queued
            if idle and self.jobs.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False

    def close(self, timeout=5.0):
        """Let the worker finish the request it holds, and stop."""
        self.stopping.set()
        if self.worker is not None:
            self.worker.join(timeout)
