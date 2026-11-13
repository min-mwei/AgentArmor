"""The summarizer daemon: keeps the embedding model resident and every session summary current.

Hooks append to a session's event log and send a datagram naming the session; the daemon reads the
new lines, embeds them and replaces summary.jsonl. The log is the source of truth, so the daemon can
be stopped at any time: on the next start it catches up from the logs, and nothing a hook wrote is
lost while it is down.
"""

import fcntl
import os
import select
import socket
import sys
import time
from collections import OrderedDict

from .index import Index
from .index import record as index_session
from .store import (
    AGENTS,
    EVENTS,
    SUMMARY,
    home,
    list_sessions,
    made_by,
    private_dir,
    read_events,
    safe_id,
    socket_path,
    write_jsonl_atomic,
)
from .summarize import Session


def _log(message):
    print(f"{time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


class Daemon:
    def __init__(
        self, runtime, max_sessions=16, catchup_hours=24.0, scan_seconds=2.0, log=_log, index=False
    ):
        self.runtime = runtime
        # The search index is written when asked for, as `summarizer serve` asks: None until
        # first needed, False when it is off or has failed. A daemon made for a replay or a
        # measurement writes none.
        self.index = None if index else False
        self.indexed = {}  # (agent, name) -> (the session read, pieces written, calls still open)
        self.sessions = OrderedDict()  # (agent, directory name) -> Session, least recent first
        self.made = {}  # summary path -> (its mtime, whether this runtime made it)
        self.max_sessions = max_sessions
        self.catchup = catchup_hours * 3600
        self.scan_seconds = scan_seconds
        self.log = log

    def update(self, agent, name):
        """Bring one session's summary up to date with its log. Returns the events consumed."""
        directory = home() / "sessions" / agent / name
        try:
            size = os.path.getsize(directory / EVENTS)
        except OSError:
            return 0
        key = (agent, name)
        session = self.sessions.get(key)
        if session is None or size < session.log_bytes:  # new here, or the log was replaced
            session = self.sessions[key] = Session(agent, name, self.runtime)
        self.sessions.move_to_end(key)
        if size == session.log_bytes:
            return 0
        started = time.perf_counter()
        records, session.log_bytes = read_events(directory / EVENTS, session.log_bytes)
        for record in records:
            session.add(record)
        session.flush()
        write_jsonl_atomic(directory / SUMMARY, session.summary())
        self._index(key, session)
        while len(self.sessions) > self.max_sessions:
            gone, _ = self.sessions.popitem(last=False)
            self.indexed.pop(gone, None)
        elapsed = (time.perf_counter() - started) * 1000
        self.log(
            f"{agent}/{name}: +{len(records)} events, {len(session.units)} units, {elapsed:.0f} ms"
        )
        return len(records)

    def _index(self, key, session):
        """Bring the search index up to this session. The index is a help and is never in the
        way: whatever goes wrong here is said once, and summarizing goes on without it. An
        index that stopped being written says so itself, by how far it has read."""
        if self.index is False:
            return
        try:
            if self.index is None:
                self.index = Index(write=True)
            known = self.indexed.get(key)
            # Another Session object for the same key is the log read again from its start.
            start, open_calls = (known[1], known[2]) if known and known[0] is session else (0, ())
            written = index_session(self.index, key[0], key[1], session, start, open_calls)
            # A call's status follows what becomes of it, so a proposed call is written again.
            still = {
                row["unit"]
                for row in written
                if row["kind"] == "action" and row["status"] == "proposed"
            }
            self.indexed[key] = (session, len(session.units), still)
        except Exception as error:  # noqa: BLE001 - nothing here may stop a summary
            self.index = False
            self.log(f"search index off: {type(error).__name__}: {error}")

    def _mine(self, directory):
        """Whether the stored summary of a session was made by this daemon's runtime: the
        same encoder, probes and rules. Read once for each version of the file."""
        path = directory / SUMMARY
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return False
        known = self.made.get(path)
        if known is None or known[0] != mtime:
            known = self.made[path] = (mtime, made_by(directory) == self.runtime.identity())
            while len(self.made) > 4096:
                self.made.pop(next(iter(self.made)))
        return known[1]

    def stale(self):
        """Sessions whose log has moved past their summary, and sessions whose summary was
        made by another encoder, other probes or other rules than this daemon's.

        A summary that covers every event is not thereby current: after a model or its
        probes are replaced, it still carries the old marks and vectors. Those within the
        catch-up window are rebuilt from the log. Older ones are left as they are and say
        what made them, which load_summary(made_with=...) holds against a runtime.
        """
        now, found = time.time(), set()
        for agent, name, directory, mtime in list_sessions():
            session = self.sessions.get((agent, name))
            if session is not None:
                if os.path.getsize(directory / EVENTS) != session.log_bytes:
                    found.add((agent, name))
                continue
            if now - mtime > self.catchup:
                continue
            try:
                current = os.path.getmtime(directory / SUMMARY) >= mtime
            except OSError:
                current = False
            if not current or not self._mine(directory):
                found.add((agent, name))
        return found

    def _bind(self):
        path = socket_path()
        private_dir(path.parent)
        self._lock = open(path.with_name("daemon.lock"), "w")  # noqa: SIM115 - held for our lifetime
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(f"another summarizer daemon already serves {home()}") from None
        try:
            path.unlink(missing_ok=True)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.bind(str(path))
            os.chmod(path, 0o600)
            sock.setblocking(False)
            return sock
        except OSError as error:  # for example a home directory too deep for a socket path
            self.log(f"no nudge socket ({error}); relying on rescans")
            self.scan_seconds = min(self.scan_seconds, 0.25)
            return None

    def _nudged(self, sock, timeout):
        dirty = set()
        if sock is None:
            time.sleep(timeout)
            return dirty
        if select.select([sock], [], [], timeout)[0]:
            while True:
                try:
                    data = sock.recv(4096)
                except BlockingIOError:
                    break
                agent, _, session_id = data.decode("utf-8", "replace").partition("\t")
                if agent in AGENTS and session_id:
                    dirty.add((agent, safe_id(session_id)))
        return dirty

    def serve(self, stop=None):
        """Run until stop (a threading.Event) is set."""
        sock = self._bind()
        self.log(f"serving {home()} with {self.runtime.embedder.info}")
        scanned = 0.0
        try:
            while stop is None or not stop.is_set():
                dirty = self._nudged(sock, 0.25)
                if time.monotonic() - scanned >= self.scan_seconds:
                    dirty |= self.stale()
                    scanned = time.monotonic()
                for agent, name in sorted(dirty):
                    try:
                        self.update(agent, name)
                    except Exception as error:  # one bad session must not stop the others
                        self.log(f"{agent}/{name}: update failed: {error!r}")
        finally:
            if sock is not None:
                sock.close()
                socket_path().unlink(missing_ok=True)
            self._lock.close()
