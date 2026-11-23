"""What can be read, without running anything, of whether a gate is in an agent's way.

    agentarmor-monitor doctor --agent claude_code [--project DIR]

Printing a configuration is not installing it, and an installed hook is not a call that
was stopped. This reads the places an agent keeps its hooks and reports four things apart,
because each can hold while the next does not:

- registration: which settings files register this monitor's gate, for which events, with
  which program, in shadow or not, and (Claude Code) whether a failing hook blocks the call;
- program: whether the registered program is an absolute path to a file that can be run;
- mode: what the registrations say against what the local server says of itself;
- server: the local server's own account (the caller adds it).

It changes nothing and runs nothing, so it never says that interception works. Whether the
client trusts and runs a registered hook is the client's to know: Codex asks the user to
review hooks, and both clients can be managed by settings this does not read. "exercised"
is therefore always false here. Only a call that a real client was seen not to run shows
that a deny stops a call.
"""

import json
import os
import shlex
import tomllib
from pathlib import Path

GATE_EVENTS = ("PreToolUse",)  # where a deny can stop a call
PROGRAM = "agentarmor-monitor"
MANAGED = {
    "claude_code": ("/Library/Application Support/ClaudeCode/managed-settings.json",),
    "codex_code": ("/etc/codex/requirements.toml", "/etc/codex/managed_config.toml"),
}


def places(agent, project=None, env=None, home=None):
    """The settings files an agent reads hooks from, most personal first: [(scope, path)]."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    project = Path(project) if project else None
    if agent == "claude_code":
        user = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude")
        found = [("user", user / "settings.json")]
        if project:
            found += [("project", project / ".claude" / "settings.json")]
            found += [("project_local", project / ".claude" / "settings.local.json")]
    elif agent == "codex_code":
        user = Path(env.get("CODEX_HOME") or home / ".codex")
        found = [("user", user / "hooks.json"), ("user", user / "config.toml")]
        if project:
            found += [("project", project / ".codex" / "hooks.json")]
            found += [("project", project / ".codex" / "config.toml")]
    else:
        raise ValueError(f"unknown agent: {agent!r}")
    return found + [("managed", Path(path)) for path in MANAGED[agent]]


def _words(handler):
    """(program, arguments) of one hook handler, however the client writes it."""
    command, arguments = handler.get("command"), handler.get("args")
    if not isinstance(command, str):
        return None, []
    if isinstance(arguments, list):  # Claude Code: a program and its arguments
        return command, [str(word) for word in arguments]
    try:
        words = shlex.split(command)  # Codex: one command line
    except ValueError:
        return None, []
    return (words[0], words[1:]) if words else (None, [])


def gates_in(settings):
    """This monitor's gate handlers in one parsed settings file: [{"event", "program",
    "shadow", "on_failure", "agent"}]. Any other hook is not ours and is not reported."""
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    found = []
    for event, groups in (hooks if isinstance(hooks, dict) else {}).items():
        for group in groups if isinstance(groups, list) else ():
            handlers = group.get("hooks") if isinstance(group, dict) else None
            for handler in handlers if isinstance(handlers, list) else ():
                if not isinstance(handler, dict):
                    continue
                program, words = _words(handler)
                if not program or Path(program).name != PROGRAM or words[:1] != ["gate"]:
                    continue
                agent = words[words.index("--agent") + 1] if "--agent" in words[:-1] else None
                found.append(
                    {"event": event, "program": program, "shadow": "--shadow" in words}
                    | {"on_failure": handler.get("onFailure"), "agent": agent}
                )
    return found


def read(path):
    """(settings, why not): a settings file parsed, or why it could not be."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "absent"
    except (OSError, UnicodeDecodeError):
        return None, "unreadable"
    try:
        return (tomllib.loads(raw) if path.suffix == ".toml" else json.loads(raw)), None
    except (ValueError, tomllib.TOMLDecodeError):
        return None, "not_parsed"


def program_of(path):
    """What can be said of a registered program without running it."""
    found = {"path": path, "absolute": os.path.isabs(path)}
    found["exists"] = os.path.isfile(path) if found["absolute"] else None
    found["executable"] = os.access(path, os.X_OK) if found["exists"] else None
    return found


def report(agent, project=None, env=None, home=None, server=None):
    """Registration, program and mode for one agent, each said apart. server is the local
    server's health, or None when it did not answer."""
    files, gates = [], []
    for scope, path in places(agent, project, env, home):
        settings, why = read(path)
        mine = gates_in(settings) if settings is not None else []
        files.append({"scope": scope, "path": str(path), "state": why or "read"})
        files[-1]["gate_events"] = sorted({gate["event"] for gate in mine})
        gates += [gate | {"scope": scope, "file": str(path)} for gate in mine]
    stopping = [gate for gate in gates if gate["event"] in GATE_EVENTS]
    programs = sorted({gate["program"] for gate in gates})
    registration = {"files": files, "registered": bool(stopping)}
    registration["events"] = sorted({gate["event"] for gate in gates})
    # A gate registered in two files runs twice for one call, and can answer twice.
    registration["registered_more_than_once"] = len({g["file"] for g in stopping}) > 1
    registration["other_agent"] = sorted(
        {gate["agent"] for gate in gates if gate["agent"] not in (None, agent)}
    )
    shadows = {gate["shadow"] for gate in stopping}
    mode = {"gate": None if not stopping else "mixed" if len(shadows) > 1 else None}
    if stopping and len(shadows) == 1:
        mode["gate"] = "shadow" if shadows.pop() else "enforce"
    blocks = {gate["on_failure"] == "block" for gate in stopping}
    # Only Claude Code can be told to stop a call when its hook fails; elsewhere not known.
    mode["a_failing_gate_blocks"] = (blocks == {True}) if agent == "claude_code" else None
    mode["server"] = ((server or {}).get("decisions") or {}).get("mode")
    mode["agree"] = (
        None if None in (mode["gate"], mode["server"]) else (mode["gate"] == mode["server"])
    )
    told = {"agent": agent, "registration": registration}
    told["program"] = [program_of(path) for path in programs]
    told["mode"] = mode
    # Never claimed from reading files: trust, an actual invocation, and a call that a
    # real client did not run after a deny.
    told["client_trust"] = "not_readable"
    told["exercised"] = False
    return told
