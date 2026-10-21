import json
import os
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import pytest

from armor import hooks


@pytest.mark.parametrize("agent", ["codex_code", "claude_code"])
def test_missing_configuration_explicitly_denies(agent, repo_root):
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AGENTARMOR_")
    }
    result = subprocess.run(
        [sys.executable, str(repo_root / "agents" / agent / "hook.py")],
        input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}),
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_read_submits_actual_file_text(monkeypatch, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("file contents αβ", encoding="utf-8")
    captured = []
    monkeypatch.setattr(
        hooks, "submit_scan", lambda *args: captured.append(args) or {"id": "scan-123"}
    )
    monkeypatch.setattr(hooks, "wait_for_scan", lambda job_id: {"suspicious": False})
    assert (
        hooks.background_scan(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": str(path)},
                "tool_response": "partial response",
            }
        )
        is None
    )
    assert captured == [("file contents αβ", "text_file", str(path))]


def test_chat_scan_includes_log_and_pending_prompt(monkeypatch, tmp_path):
    path = tmp_path / "chat.jsonl"
    path.write_text('{"role":"assistant","content":"Earlier answer"}\n')
    captured = []
    monkeypatch.setattr(
        hooks, "submit_scan", lambda *args: captured.append(args) or {"id": "scan-123"}
    )
    monkeypatch.setattr(hooks, "wait_for_scan", lambda job_id: {"suspicious": False})
    hooks.background_scan(
        {
            "hook_event_name": "UserPromptSubmit",
            "transcript_path": str(path),
            "prompt": "New task",
        }
    )
    assert "Earlier answer" in captured[0][0]
    assert "New task" in captured[0][0]
    assert captured[0][1] == "chat_log"


def test_stop_scan_includes_final_assistant_message(monkeypatch, tmp_path):
    path = tmp_path / "chat.jsonl"
    path.write_text('{"role":"user","content":"Earlier task"}\n')
    captured = []
    monkeypatch.setattr(
        hooks, "submit_scan", lambda *args: captured.append(args) or {"id": "scan-123"}
    )

    def must_not_wait(job_id):
        pytest.fail("Stop must enqueue and return before the native CLI exits")

    monkeypatch.setattr(hooks, "wait_for_scan", must_not_wait)
    result = hooks.background_scan(
        {
            "hook_event_name": "Stop",
            "transcript_path": str(path),
            "last_assistant_message": "Final response αβ",
        }
    )
    assert "Earlier task" in captured[0][0]
    assert "Final response αβ" in captured[0][0]
    assert "scan-123" in result["systemMessage"]


def test_templates_enable_nonblocking_scans(repo_root):
    paths = ["agents/codex_code/hooks.json.example", "agents/claude_code/settings.json.example"]
    configurations = [json.loads((repo_root / path).read_text())["hooks"] for path in paths]
    configurations.append(
        tomllib.loads((repo_root / "agents/codex_code/config.toml.example").read_text())["hooks"]
    )
    for configuration in configurations:
        assert not configuration["PreToolUse"][0]["hooks"][0].get("async", False)
        assert not configuration["Stop"][0]["hooks"][0].get("async", False)
        for event in ("PostToolUse", "UserPromptSubmit"):
            assert configuration[event][0]["hooks"][0]["async"] is True


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_allowed_checks_abstain_from_agent_permissions(monkeypatch, decision):
    monkeypatch.setattr(hooks, "operator_context", lambda: {"policy": "{}", "task": "task"})
    monkeypatch.setattr(hooks, "request_json", lambda *args, **kwargs: {"decision": decision})
    output = hooks.check(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {},
        },
        "codex_code",
    )
    if decision == "allow":
        assert output is None
    else:
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
