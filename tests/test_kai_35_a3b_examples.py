"""The example requests for Kai-35-A3B are what the monitor's renderers produce today."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("summarizer")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "kai-35-a3b"
sys.path[:0] = [str(ROOT / "src" / "monitor"), str(ROOT / "src" / "summarizer")]

from agentarmor_monitor import kai, policy  # noqa: E402


def test_the_committed_examples_equal_a_rebuild():
    done = subprocess.run(
        [sys.executable, str(EXAMPLES / "make.py"), "--check"], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("name", ["content-attempt", "content-clean", "tool-call"])
def test_an_example_asks_the_one_trained_question(name):
    request = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    assert request["questions"] == {kai.DECISION: kai.QUESTIONS[kai.DECISION]}
    assert request["state"].count("\nProposed tool call:\n") == 1


@pytest.mark.parametrize("name", ["content-attempt", "content-clean"])
def test_a_content_example_is_a_content_decision(name):
    state = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))["state"]
    page = state.split("Conversation so far:\n", 1)[1].rsplit("\n\nProposed tool call:", 1)[0]
    assert policy.content_state(page) == state
    assert "tool: return_result_to_agent" in state


def test_the_setup_script_names_the_examples_and_is_valid_shell():
    script = ROOT / "scripts" / "try-kai-35-a3b.sh"
    text = script.read_text(encoding="utf-8")
    for name in ("content-attempt", "content-clean", "tool-call"):
        assert name in text and (EXAMPLES / f"{name}.json").is_file()
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
