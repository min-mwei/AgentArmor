"""Per-session spool layout. Standard library only: the hook imports this on every agent event.

$SUMMARIZER_HOME/                      default ~/.summarizer, private to the user
  sessions/<agent>/<session>/events.jsonl    append-only hook events (the source of truth)
  sessions/<agent>/<session>/summary.jsonl   latest summary, replaced whole by the daemon
  run/daemon.sock                            datagram socket the hook nudges

Both files are JSON Lines. summary.jsonl holds one record per line: a "session" record, then the
entries ("prompt", "action", "content", "message") in session order, then the observables ("ioc"),
the session "vectors" and a text "digest". It is replaced atomically, so a reader always sees one
consistent summary.
"""

import fcntl
import hashlib
import json
import os
import re
import time
from pathlib import Path

AGENTS = ("claude_code", "codex_code")
EVENTS = "events.jsonl"
SUMMARY = "summary.jsonl"
ENTRY_TYPES = ("prompt", "action", "content", "message")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def home():
    return Path(os.environ.get("SUMMARIZER_HOME") or Path.home() / ".summarizer")


def private_dir(path):
    """Create path and any missing parents below the home directory with mode 0700."""
    path = Path(path)
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for item in reversed(missing):
        try:
            os.mkdir(item, 0o700)
        except FileExistsError:  # another hook created it first
            pass


def safe_id(value):
    """Map an agent-supplied session id to exactly one path component."""
    value = str(value)
    if _SAFE_ID.fullmatch(value):
        return value
    return "h-" + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:40]


def session_dir(agent, session_id):
    if agent not in AGENTS:
        raise ValueError(f"unknown agent: {agent!r}")
    return home() / "sessions" / agent / safe_id(session_id)


def socket_path():
    return home() / "run" / "daemon.sock"


def append_event(agent, session_id, record):
    """Append one JSON line; O_APPEND and an exclusive lock keep concurrent hooks apart."""
    directory = session_dir(agent, session_id)
    private_dir(directory)
    # ensure_ascii keeps lone surrogates from hook payloads encodable.
    line = (json.dumps(record, separators=(",", ":")) + "\n").encode("ascii")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(directory / EVENTS, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = memoryview(line)
        while data:
            data = data[os.write(fd, data) :]
    finally:
        os.close(fd)
    return directory


def read_events(path, offset=0, limit=None):
    """Return (records, new_offset), consuming complete lines only, at most limit of them.

    A line that does not parse becomes {"corrupt": True} so coverage accounting can see it.
    """
    with open(path, "rb") as handle:
        handle.seek(offset)
        data = handle.read()
    records, consumed = [], 0
    for line in data[: data.rfind(b"\n") + 1].splitlines(keepends=True):
        if limit is not None and len(records) >= limit:
            break
        consumed += len(line)
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        records.append(record if isinstance(record, dict) else {"corrupt": True})
    return records, offset + consumed


def write_jsonl_atomic(path, records):
    """Replace path with records, one JSON object per line."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def view(records):
    """Group summary records for reading: the session, its entries, observables, vectors, text."""
    grouped = {"session": None, "entries": [], "iocs": [], "vectors": None, "text": ""}
    for record in records:
        kind = record.get("type")
        if kind == "session":
            grouped["session"] = record
        elif kind in ENTRY_TYPES:
            grouped["entries"].append(record)
        elif kind == "ioc":
            grouped["iocs"].append(record)
        elif kind == "vectors":
            grouped["vectors"] = record
        elif kind == "digest":
            grouped["text"] = record.get("text", "")
    return grouped if grouped["session"] else None


def call_state(summary, call_id, call_sha256=None):
    """Whether a summary holds the tool call a guard is deciding about, and whether it is the same.

    - "verified": the call is among the entries and its logged identity equals call_sha256, the
      guard's own hook.call_sha256(tool_name, tool_input).
    - "unverified": the call is there but could not be compared: no call_sha256 was given, or the
      log holds none for it. The id matches; nothing says the tool and arguments do.
    - "mismatched": the call is there with a different tool or input than the guard holds.
    - "missing": no entry has that id. A fresh summary can still be missing a call: freshness is
      about the event log, not about any one call.

    Only "verified" says the summary is about the call in hand.
    """
    for entry in summary["entries"]:
        if entry.get("type") == "action" and entry.get("call_id") == call_id:
            known = entry.get("call_sha256")
            if not call_sha256 or not known:
                return "unverified"
            return "verified" if call_sha256 == known else "mismatched"
    return "missing"


def _read_summary(directory):
    try:
        with open(directory / SUMMARY, encoding="utf-8") as handle:
            summary = view([json.loads(line) for line in handle if line.strip()])
    except (OSError, ValueError):
        return None
    if summary is None:
        return None
    try:
        size = os.path.getsize(directory / EVENTS)
    except OSError:
        size = 0
    # fresh: the summary covers every event the hooks have logged so far.
    summary["fresh"] = summary["session"]["coverage"].get("log_bytes") == size
    return summary


def made_by(directory):
    """What a session's stored summary says made it: {"embedding", "detector"}, or None when
    there is no summary or it does not say. Only its first record is read."""
    try:
        with open(directory / SUMMARY, encoding="utf-8") as handle:
            first = json.loads(handle.readline())
    except (OSError, ValueError):
        return None
    if not isinstance(first, dict) or first.get("type") != "session":
        return None
    return {"embedding": first.get("embedding"), "detector": first.get("detector")}


def load_summary(agent, session_id, wait=0.0, call_id=None, call_sha256=None, made_with=None):
    """Return the latest summary of a session, or None if there is none yet.

    The result groups the lines of summary.jsonl: "session" (the session record), "entries",
    "iocs", "vectors" and "text" (the digest). These fields say how far it can be relied on:

    - "fresh" is True when the summary covers every event logged so far.
    - "call", present when call_id is given, is call_state() for that tool call: "verified",
      "unverified", "mismatched" or "missing".
    - "compatible", present when made_with is given (a runtime's identity()), is True when
      the summary was made by that encoder, those probes and those rules. A summary that
      covers every event can still be one another model made: fresh says nothing of that.

    With wait > 0, poll up to that many seconds for a fresh summary that includes call_id (a
    PreToolUse guard races this project's own PreToolUse hook for the same call). On timeout the
    latest summary is returned as is, so check both fields: a fresh summary without the call is
    not a basis for deciding about that call.
    """
    directory = session_dir(agent, session_id)
    deadline = time.monotonic() + wait
    while True:
        summary = _read_summary(directory)
        if summary is not None and call_id is not None:
            summary["call"] = call_state(summary, call_id, call_sha256)
        if summary is not None and made_with is not None:
            session = summary["session"]
            made = {"embedding": session.get("embedding"), "detector": session.get("detector")}
            summary["compatible"] = made == made_with
        ready = summary is not None and summary["fresh"] and summary.get("call") != "missing"
        if ready or time.monotonic() >= deadline:
            return summary
        time.sleep(0.02)


def list_sessions():
    """Return (agent, session, directory, log mtime) for every spooled session, newest first."""
    found = []
    for agent in AGENTS:
        root = home() / "sessions" / agent
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            try:
                mtime = os.path.getmtime(os.path.join(entry.path, EVENTS))
            except OSError:
                continue
            found.append((agent, entry.name, Path(entry.path), mtime))
    return sorted(found, key=lambda item: -item[3])
