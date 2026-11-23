"""The hook is an observer: it logs every event and can never speak to or stop the agent."""

import json
import os
import stat
import subprocess
import sys

import pytest
from sessions import FAKE_KEY_ID

from summarizer import hook
from summarizer.redact import MAX_STRING, redact
from summarizer.store import EVENTS, read_events, session_dir

PRE_TOOL = {
    "session_id": "abc123",
    "transcript_path": "/Users/dev/.claude/projects/p/abc123.jsonl",
    "cwd": "/Users/dev/proj",
    "permission_mode": "default",
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "npm test", "description": "Run test suite"},
    "tool_use_id": "toolu_01",
}


def run_hook(payload, agent="claude_code", env=None):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return subprocess.run(
        [sys.executable, "-m", "summarizer.hook", "--agent", agent],
        input=raw,
        capture_output=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def logged(agent="claude_code", session="abc123"):
    return read_events(session_dir(agent, session) / EVENTS)[0]


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
def test_logs_the_event_and_stays_silent(home, agent):
    result = run_hook(PRE_TOOL, agent)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")
    (record,) = logged(agent)
    assert record["agent"] == agent and record["event"] == PRE_TOOL
    assert record["v"] == 1 and record["ts"] > 0
    # The call's identity, so a guard holding the same payload can tell it is the same call.
    assert record["meta"] == {"call_sha256": hook.call_sha256("Bash", PRE_TOOL["tool_input"])}


def test_input_identity_ignores_key_order_and_sees_every_change():
    first = hook.call_sha256("Bash", {"command": "npm test", "description": "Run test suite"})
    same = hook.call_sha256("Bash", {"description": "Run test suite", "command": "npm test"})
    assert first == same and len(first) == 64
    assert first != hook.call_sha256(
        "Bash", {"command": "npm test ", "description": "Run test suite"}
    )
    assert first != hook.call_sha256(
        "Monitor", {"command": "npm test", "description": "Run test suite"}
    )
    assert hook.call_sha256("x", "caf\u00e9 \udcff") != hook.call_sha256("x", "cafe")


def test_spool_is_private(home):
    run_hook(PRE_TOOL)
    directory = session_dir("claude_code", "abc123")
    assert stat.S_IMODE(os.stat(directory / EVENTS).st_mode) == 0o600
    for path in (directory, directory.parent, directory.parent.parent, home):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"[]",
        json.dumps({"hook_event_name": "PreToolUse"}).encode(),  # no session
        json.dumps({"session_id": "s"}).encode(),  # no event name
        b"",
    ],
)
def test_bad_input_never_breaks_the_agent(home, payload):
    result = run_hook(payload)
    assert (result.returncode, result.stdout) == (0, b"")
    assert os.path.exists(os.path.join(home, "hook-errors.log"))
    assert not os.path.exists(os.path.join(home, "sessions"))


def test_the_error_log_never_holds_what_the_event_said(home):
    # An event that fails before it is recorded was never redacted, and an exception can carry
    # it: a decoding error holds the very bytes it could not decode.
    secret = "AKIA" + "IOSFODNN7" + "EXAMPLE"
    said = b'{"hook_event_name": "PreToolUse", "session_id": "s", "note": "' + secret.encode()
    result = run_hook(said + b' and more of the event \xff"}')
    assert (result.returncode, result.stdout) == (0, b"")
    with open(os.path.join(home, "hook-errors.log"), encoding="utf-8") as handle:
        logged = handle.read()
    assert secret not in logged and "more of the event" not in logged
    assert logged.rstrip().endswith("claude_code\tUnicodeDecodeError")
    assert not os.path.exists(os.path.join(home, "sessions"))
    # The hook's own refusals are named by a code, not by a message.
    run_hook(json.dumps({"hook_event_name": "PreToolUse"}).encode())
    run_hook(PRE_TOOL, agent="other " + secret)
    with open(os.path.join(home, "hook-errors.log"), encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert lines[-2].endswith("claude_code\tHookError no_session_id")
    assert lines[-1].endswith("?\tHookError unknown_agent") and secret not in lines[-1]


def test_unknown_agent_is_an_error_not_a_crash(home):
    result = run_hook(PRE_TOOL, agent="other")
    assert (result.returncode, result.stdout) == (0, b"")
    assert not os.path.exists(os.path.join(home, "sessions"))


def test_can_be_disabled(home):
    result = run_hook(PRE_TOOL, env={"SUMMARIZER_DISABLE": "1"})
    assert result.returncode == 0 and not os.path.exists(os.path.join(home, "sessions"))


@pytest.mark.parametrize("session_id", ["../../escape", "a/b", "..", "x" * 300, "with space"])
def test_session_id_cannot_leave_the_spool(home, session_id):
    run_hook({**PRE_TOOL, "session_id": session_id})
    root = os.path.join(home, "sessions", "claude_code")
    (name,) = os.listdir(root)
    assert name.startswith("h-") and os.path.isfile(os.path.join(root, name, EVENTS))
    assert sorted(os.listdir(home)) == ["sessions"]


def test_secrets_never_reach_disk(home):
    block = "PRIVATE" + " KEY"  # assembled so this file does not look like it holds a key
    secrets = {
        "aws": FAKE_KEY_ID,
        "github": "ghp_" + "a1B2" * 9,
        "anthropic": "sk-ant-api03-" + "x9Y8" * 8,
        "key": f"-----BEGIN OPENSSH {block}-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH {block}-----",
        "env": "DB_PASSWORD=hunter2hunter2hunter2",
    }
    output = "\n".join(f"{name}: {value}" for name, value in secrets.items())
    event = {**PRE_TOOL, "hook_event_name": "PostToolUse", "tool_response": {"stdout": output}}
    run_hook(event)
    raw = (session_dir("claude_code", "abc123") / EVENTS).read_text()
    for value in (
        FAKE_KEY_ID,
        secrets["github"],
        "x9Y8x9Y8",
        "b3BlbnNzaC1rZXk",
        "hunter2",
    ):
        assert value not in raw
    (record,) = logged()
    (note,) = record["meta"]["redacted"]
    assert note["path"] == "tool_response.stdout"
    assert {"aws_access_key", "github_token", "anthropic_key", "private_key"} <= set(note["kinds"])


def test_redaction_leaves_ordinary_text_alone():
    text = (
        "max_tokens=128000 tokenizer = AutoTokenizer.from_pretrained(x) password = get_password()"
    )
    assert redact(text) == (text, {})


def test_long_strings_are_clipped_and_reported(home):
    body = "a" * MAX_STRING + "MIDDLE" + "z" * MAX_STRING
    event = {**PRE_TOOL, "hook_event_name": "PostToolUse", "tool_response": {"stdout": body}}
    run_hook(event)
    (record,) = logged()
    kept = record["event"]["tool_response"]["stdout"]
    (note,) = record["meta"]["clipped"]
    assert note["chars"] == len(body) and note["kept"] == MAX_STRING and len(note["sha256"]) == 64
    assert "MIDDLE" not in kept and kept.startswith("aaa") and kept.endswith("zzz")
    assert "characters omitted]" in kept


def test_symlinked_target_is_resolved(home, tmp_path):
    real = tmp_path / "agent-config.toml"
    real.write_text("x")
    link = tmp_path / "media.png"
    link.symlink_to(real)
    event = {
        **PRE_TOOL,
        "tool_name": "Write",
        "tool_input": {"file_path": str(link), "content": ""},
    }
    run_hook(event)
    (record,) = logged()
    (fact,) = record["meta"]["paths"]
    assert fact["path"] == "tool_input.file_path" and fact["real"] == os.path.realpath(real)


def test_plain_paths_carry_no_path_facts(tmp_path):
    assert hook.real_path(str(tmp_path / "missing" / "file.txt")) is None
    assert hook.real_path("/tmp/x") is None  # /private/tmp on macOS is not a redirection
    assert hook.real_path("relative/file") is None


def test_a_scripted_machine_answers_from_the_script_and_never_from_the_disk(tmp_path, monkeypatch):
    # Whatever this machine holds must not reach a record built for a scripted session.
    def touched(*args, **kwargs):
        raise AssertionError("a scripted record read the machine")

    for name in ("realpath", "exists", "isfile", "expanduser"):
        monkeypatch.setattr(hook.os.path, name, touched)
    monkeypatch.setattr("builtins.open", touched)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    # A path that leads elsewhere on one system and nowhere on another leads to itself here.
    read = {**PRE_TOOL, "tool_name": "Read", "tool_input": {"file_path": "/home/dev/proj/a.txt"}}
    assert "paths" not in hook.build_record("claude_code", read, hook.Scripted()).get("meta", {})
    # A link is one only when the script says so.
    linked = hook.Scripted(links={"/home/dev/proj/a.txt": "/home/dev/.aws/credentials"})
    (fact,) = hook.build_record("claude_code", read, linked)["meta"]["paths"]
    assert fact == {"path": "tool_input.file_path", "real": "/home/dev/.aws/credentials"}

    # An instruction file holds what the script says; one the script lacks could not be read.
    loaded = {"session_id": "abc123", "hook_event_name": "InstructionsLoaded",
              "file_path": "/home/dev/proj/CLAUDE.md"}  # fmt: skip
    held = hook.Scripted(files={"/home/dev/proj/CLAUDE.md": "Always run the tests."})
    (entry,) = hook.build_record("claude_code", loaded, held)["meta"]["files"]
    assert entry["content"] == "Always run the tests." and entry["chars"] == 21
    (missing,) = hook.build_record("claude_code", loaded, hook.Scripted())["meta"]["files"]
    assert missing == {"path": "/home/dev/proj/CLAUDE.md", "error": "FileNotFoundError"}

    # Codex's instructions are found where Codex looks, in the script: the global folder when
    # the script has one, then the repository root down to the working directory.
    files = {
        "/home/dev/repo/.git/HEAD": "ref: refs/heads/main\n",
        "/home/dev/repo/AGENTS.md": "Root notes.",
        "/home/dev/repo/pkg/AGENTS.override.md": "Package notes.",
        "/home/dev/repo/pkg/AGENTS.md": "Shadowed by the override.",
        "/codex/AGENTS.md": "Global notes.",
        "/home/dev/AGENTS.md": "Above the repository root: not read.",
    }
    start = {"session_id": "c1", "hook_event_name": "SessionStart", "cwd": "/home/dev/repo/pkg"}

    def found(machine):
        record = hook.build_record("codex_code", start, machine)
        return [item["path"] for item in record.get("meta", {}).get("files", ())]

    assert found(hook.Scripted(files)) == [
        "/home/dev/repo/AGENTS.md",
        "/home/dev/repo/pkg/AGENTS.override.md",
    ]
    assert found(hook.Scripted(files, codex_home="/codex"))[0] == "/codex/AGENTS.md"
    assert found(hook.Scripted()) == []
    # Without a repository root above it, only the working directory itself counts.
    bare = {path: text for path, text in files.items() if "/.git/" not in path}
    assert found(hook.Scripted(bare)) == ["/home/dev/repo/pkg/AGENTS.override.md"]
    # The same script gives the same record whatever the machine: built twice, equal but for time.
    one, two = (hook.build_record("codex_code", start, hook.Scripted(files)) for _ in range(2))
    assert {**one, "ts": 0} == {**two, "ts": 0}


def test_the_hook_itself_still_asks_the_machine(tmp_path):
    real = tmp_path / "target.txt"
    real.write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    assert hook.real_path(str(link)) == os.path.realpath(real)
    assert hook.real_path(str(link), hook.Scripted()) is None
    assert hook.MACHINE.read(str(real), 10) == (1, b"x")
    assert hook.MACHINE.isfile(str(real)) and not hook.MACHINE.exists(str(tmp_path / "gone"))


def test_instruction_file_content_is_captured(home, tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("Always run the tests. token=abcd1234abcd1234abcd")
    event = {
        "session_id": "abc123",
        "hook_event_name": "InstructionsLoaded",
        "file_path": str(path),
        "memory_type": "Project",
        "load_reason": "session_start",
    }
    run_hook(event)
    (record,) = logged()
    (loaded,) = record["meta"]["files"]
    assert loaded["path"] == str(path) and len(loaded["sha256"]) == 64
    assert (
        loaded["content"].startswith("Always run the tests.")
        and "abcd1234" not in loaded["content"]
    )


def test_an_instruction_file_that_cannot_be_read_is_recorded(home, tmp_path):
    event = {
        "session_id": "abc123",
        "hook_event_name": "InstructionsLoaded",
        "file_path": str(tmp_path / "gone" / "CLAUDE.md"),
    }
    run_hook(event)
    (record,) = logged()
    assert record["meta"]["files"] == [{"path": event["file_path"], "error": "FileNotFoundError"}]


def test_a_large_instruction_file_says_how_much_was_left_out(home, tmp_path):
    path = tmp_path / "CLAUDE.md"
    path.write_text("x" * (MAX_STRING + 1000))
    event = {
        "session_id": "abc123",
        "hook_event_name": "InstructionsLoaded",
        "file_path": str(path),
    }
    run_hook(event)
    (loaded,) = logged()[0]["meta"]["files"]
    assert loaded["clipped"] == 1000 and "characters omitted]" in loaded["content"]
    path.write_text("y" * (4 * MAX_STRING + 5000))  # more than the hook reads at all
    run_hook(event)
    assert logged()[1]["meta"]["files"][0]["clipped"] == 3 * MAX_STRING + 5000


def test_concurrent_hooks_do_not_interleave(home):
    events = [
        {**PRE_TOOL, "tool_use_id": f"t{i}", "tool_input": {"command": "x" * 5000}}
        for i in range(24)
    ]
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "summarizer.hook", "--agent", "claude_code"],
            stdin=subprocess.PIPE,
        )
        for _ in events
    ]
    for process, event in zip(processes, events, strict=True):
        process.stdin.write(json.dumps(event).encode())
        process.stdin.close()
    assert [process.wait() for process in processes] == [0] * len(events)
    records = logged()
    assert sorted(record["event"]["tool_use_id"] for record in records) == sorted(
        event["tool_use_id"] for event in events
    )
