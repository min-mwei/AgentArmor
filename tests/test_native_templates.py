"""Run the installed hook commands without a Python interpreter on PATH."""

import json
import shlex
import subprocess
import tomllib

import pytest

TEMPLATES = (
    ("codex_code", "hooks.json.example"),
    ("codex_code", "config.toml.example"),
    ("claude_code", "settings.json.example"),
)
EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")


def commands(repo_root, agent, template):
    text = (repo_root / "agents" / agent / template).read_text()
    settings = tomllib.loads(text) if template.endswith("toml.example") else json.loads(text)
    return {
        event: hook
        for event, groups in settings["hooks"].items()
        for group in groups
        for hook in group["hooks"]
    }


@pytest.mark.parametrize(("agent", "template"), TEMPLATES)
@pytest.mark.parametrize("event", EVENTS)
def test_shipped_hook_command_without_python(
    repo_root, native_binary, no_python_environment, agent, template, event
):
    hook = commands(repo_root, agent, template)[event]
    command = hook["command"].replace("/ABSOLUTE/PATH/agentarmor", str(native_binary))
    assert shlex.split(command) == [str(native_binary), "hook", "--agent", agent]
    assert hook.get("async", False) == (event in ("PostToolUse", "UserPromptSubmit"))
    result = subprocess.run(
        ["/bin/sh", "-c", command],
        input=json.dumps({"hook_event_name": event, "tool_name": "Bash", "tool_input": {}}),
        text=True,
        capture_output=True,
        env=no_python_environment,
        timeout=10,
        check=False,
    )
    output = json.loads(result.stdout)
    if event == "PreToolUse":
        assert result.returncode == 2
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    else:
        assert result.returncode == 0
        assert "incomplete" in output["systemMessage"]
        assert "hookSpecificOutput" not in output


@pytest.mark.parametrize("agent", ("codex_code", "claude_code"))
@pytest.mark.parametrize(
    "payload",
    (
        b'{"hook_event_name":"PreToolUse","secret":"SYNTHETIC"',
        b'{"hook_event_name":"PreToolUse","hook_event_name":"Stop","secret":"SYNTHETIC"}',
        b'{"hook_event_name":"PreToolUse","secret":"\xffSYNTHETIC"}',
        b'{"hook_event_name":"PreToolUse","secret":"SYNTHETIC' + b"x" * 1_000_000 + b'"}',
    ),
    ids=("malformed-json", "duplicate-event", "invalid-utf8", "oversized-input"),
)
def test_untrusted_hook_input_never_yields_clear_or_leaks_content(
    native_binary, no_python_environment, agent, payload
):
    result = subprocess.run(
        [str(native_binary), "hook", "--agent", agent],
        input=payload,
        capture_output=True,
        env=no_python_environment,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert b"denied" in result.stderr
    assert not result.stdout
    assert b"SYNTHETIC" not in result.stderr


def test_native_readiness_does_not_claim_qualified_protection(native_binary, no_python_environment):
    result = subprocess.run(
        [str(native_binary), "doctor"],
        capture_output=True,
        env=no_python_environment,
        timeout=10,
        check=True,
    )
    readiness = json.loads(result.stdout)
    assert readiness["runtime"] == "native"
    assert readiness["policy_ready"] is False
    assert readiness["model_loaded"] is False
