"""Hook entry point for Claude Code and Codex: log the event, wake the daemon, stay out of the way.

    summarizer-hook --agent claude_code|codex_code      (event JSON on stdin)

This is an observer. It never prints to stdout (both agents treat hook stdout as context or as a
decision), never blocks or denies, and exits 0 even when it fails; failures go to hook-errors.log.
Standard library only, so it starts in tens of milliseconds and cannot pull in a model runtime.
"""

import hashlib
import json
import os
import socket
import sys
import time

from .redact import MAX_STRING, clip, compact, redact
from .store import AGENTS, append_event, home, private_dir, socket_path

MAX_STDIN = 64 * 1024 * 1024
_PATH_FIELDS = ("file_path", "notebook_path", "path")
# macOS reaches these through /private; that alias is not a redirection worth reporting.
_SYSTEM_ALIASES = ("/tmp", "/var", "/etc")


class Machine:
    """What the hook asks of the machine it runs on: four questions, and nothing else.

    A hook answers them from the machine, as it must: the agent it observes reads that disk.
    A replay of a scripted session must not. There the same path would resolve differently
    from one machine to the next (/home leads elsewhere on macOS and nowhere on Linux), and an
    instruction file would be whatever the machine happens to hold. Scripted answers from the
    script instead.
    """

    def codex_home(self):
        """Where Codex keeps its global instructions, or None when there is no such place."""
        return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")

    def exists(self, path):
        return os.path.exists(path)

    def isfile(self, path):
        return os.path.isfile(path)

    def realpath(self, path):
        return os.path.realpath(path)

    def read(self, path, limit):
        """(the file's size in bytes, its first limit bytes). Raises OSError."""
        with open(path, "rb") as handle:
            return os.fstat(handle.fileno()).st_size, handle.read(limit)


class Scripted(Machine):
    """A machine that holds what a script says and nothing more.

    files maps an absolute path to the text of the file there, and links maps an absolute path
    to where it really leads. Every other path is not there and leads to itself. A directory
    is there when a file is somewhere below it, so a file under a ".git" marks a repository
    root. codex_home is the folder of Codex's global instructions, when the script has one.
    """

    def __init__(self, files=None, links=None, codex_home=None):
        self.files = {os.path.normpath(path): text for path, text in (files or {}).items()}
        self.links = {os.path.normpath(path): real for path, real in (links or {}).items()}
        self.home = codex_home

    def codex_home(self):
        return self.home

    def exists(self, path):
        path = os.path.normpath(path)
        below = path.rstrip("/") + "/"
        return path in self.files or any(name.startswith(below) for name in self.files)

    def isfile(self, path):
        return os.path.normpath(path) in self.files

    def realpath(self, path):
        return self.links.get(os.path.normpath(path), path)

    def read(self, path, limit):
        text = self.files.get(os.path.normpath(path))
        if text is None:
            raise FileNotFoundError(path)
        raw = text.encode("utf-8", "surrogatepass")
        return len(raw), raw[:limit]


MACHINE = Machine()


def real_path(path, machine=MACHINE):
    """Return the symlink-resolved path if it differs from the lexical one, else None."""
    try:
        if not os.path.isabs(path):
            return None
        lexical = os.path.normpath(path)
        real = machine.realpath(lexical)
    except (OSError, ValueError):
        return None
    alias = real.removeprefix("/private")
    if alias != real and alias.startswith(_SYSTEM_ALIASES) and not lexical.startswith("/private"):
        real = alias
    return real if real != lexical else None


def _path_facts(payload, meta, machine=MACHINE):
    tool_input = payload.get("tool_input")
    sources = [("", payload)]
    if isinstance(tool_input, dict):
        sources.append(("tool_input.", tool_input))
    for prefix, source in sources:
        for field in _PATH_FIELDS:
            value = source.get(field)
            if isinstance(value, str) and (real := real_path(value, machine)):
                meta.setdefault("paths", []).append({"path": prefix + field, "real": real})


def _codex_instructions(payload, machine=MACHINE):
    """The AGENTS.md files Codex reads when a session starts: global, then repo root down to cwd.

    Codex has no event for loading them, so the hook looks where Codex looks. Best effort: other
    file names configured as project docs are not found.
    """
    names = ("AGENTS.override.md", "AGENTS.md")
    cwd = payload.get("cwd")
    folders = [folder for folder in (machine.codex_home(),) if folder]
    if isinstance(cwd, str) and os.path.isabs(cwd):
        chain, folder = [], os.path.normpath(cwd)
        while True:
            chain.append(folder)
            parent = os.path.dirname(folder)
            if machine.exists(os.path.join(folder, ".git")) or parent == folder:
                break
            folder = parent
        # Without a repository root above it, only the working directory itself counts.
        rooted = machine.exists(os.path.join(chain[-1], ".git"))
        folders += reversed(chain) if rooted else chain[:1]
    found = []
    for folder in folders:
        path = next((p for n in names if machine.isfile(p := os.path.join(folder, n))), None)
        if path:
            found.append(path)
    return found


def _capture(path, meta, machine=MACHINE):
    """Keep what an instruction file said: the agent took it as authority without a tool call."""
    if not isinstance(path, str) or not os.path.isabs(path):
        return
    try:
        size, raw = machine.read(path, 4 * MAX_STRING)
    except OSError as error:  # the agent may still have read it; say that we could not
        meta.setdefault("files", []).append({"path": path, "error": type(error).__name__})
        return
    text = raw.decode("utf-8", "replace")
    entry = {"path": path, "chars": len(text), "sha256": hashlib.sha256(raw).hexdigest()}
    text, kinds = redact(text)
    text, note = clip(text)
    if kinds:
        entry["redacted"] = kinds
    if note or size > len(raw):
        # How much of the file is not in content: cut from the middle here, or never read.
        cut = note["chars"] - note["kept"] if note else 0
        entry["clipped"] = cut + max(0, size - len(raw))
    entry["content"] = text
    meta.setdefault("files", []).append(entry)


def call_sha256(tool_name, tool_input):
    """Identity of a tool call: SHA-256 of the canonical JSON of its tool name and input.

    It binds which tool and which arguments, not where the call runs: compare the working
    directory and session yourself.
    """
    canonical = json.dumps([tool_name, tool_input], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def build_record(agent, payload, machine=MACHINE):
    """The record the hook logs for one event.

    machine answers what the hook asks of the disk. A hook uses the machine it runs on. A
    replay of a scripted session passes a Scripted one, so that the record holds only what
    the script says and is the same wherever it is built.
    """
    meta = {}
    event = compact(payload, meta)
    _path_facts(payload, meta, machine)
    if "tool_input" in payload:
        # Hashed before redaction, so a guard holding the same call can check it is the same one.
        meta["call_sha256"] = call_sha256(payload.get("tool_name"), payload["tool_input"])
    name = payload.get("hook_event_name")
    if name == "InstructionsLoaded":
        _capture(payload.get("file_path"), meta, machine)
    elif name == "SessionStart" and agent == "codex_code":
        for path in _codex_instructions(payload, machine):
            _capture(path, meta, machine)
    record = {"v": 1, "ts": round(time.time(), 3), "agent": agent, "event": event}
    if meta:
        record["meta"] = meta
    return record


def nudge(agent, session_id):
    """Tell a running daemon which session changed. Best effort: the daemon also rescans."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            sock.sendto(f"{agent}\t{session_id}".encode(), str(socket_path()))
    except (OSError, ValueError):
        pass


class HookError(ValueError):
    """An event this hook refused, for a reason it can name without quoting the event."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def error_note(error):
    """What may be written about a failure: its class and a code, never its text.

    An exception's message or arguments can hold the event itself (a decoding error carries
    the bytes it could not decode), and an event that failed here was never redacted.
    """
    note = type(error).__name__
    if isinstance(error, HookError):
        note += f" {error.code}"
    elif isinstance(error, OSError) and isinstance(error.errno, int):
        note += f" errno={error.errno}"
    return note


def _log_error(agent, error):
    try:
        private_dir(home())
        path = home() / "hook-errors.log"
        mode = "a" if not path.exists() or path.stat().st_size < 1_000_000 else "w"
        named = agent if agent in AGENTS else "?"  # the word given on the command line, if known
        with open(path, mode, encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{named}\t{error_note(error)}\n")
        os.chmod(path, 0o600)
    except OSError:
        pass


def handle(agent, raw, machine=MACHINE):
    """Log one hook event given as its raw JSON. machine is as for build_record()."""
    if agent not in AGENTS:
        raise HookError("unknown_agent", f"--agent must be one of {AGENTS}")
    if len(raw) > MAX_STDIN:
        raise HookError("too_large", f"hook event larger than {MAX_STDIN} bytes was not logged")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("hook_event_name"), str):
        raise HookError("no_event_name", "hook event has no hook_event_name")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise HookError("no_session_id", "hook event has no session_id")
    append_event(agent, session_id, build_record(agent, payload, machine))
    nudge(agent, session_id)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    agent = argv[argv.index("--agent") + 1] if "--agent" in argv[:-1] else ""
    if os.environ.get("SUMMARIZER_DISABLE") == "1":
        return 0
    try:
        handle(agent, sys.stdin.buffer.read(MAX_STDIN + 1))
    except Exception as error:  # an observer must never break the agent it observes
        _log_error(agent, error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
