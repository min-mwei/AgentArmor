"""The installable hook examples under agents/ are what the monitor's own generator prints."""

import json
import tomllib

import pytest
from agentarmor_monitor.cli import hooks
from summarizer.cli import hooks_config

GATE = "/ABSOLUTE/PATH/agentarmor-monitor"
SHIPPED = {"claude_code": "settings.json.example", "codex_code": "hooks.json.example"}


@pytest.mark.parametrize("agent", sorted(SHIPPED))
def test_the_shipped_example_is_the_generated_shadow_gate(repo_root, agent):
    made = hooks(agent, gate=GATE, shadow=True)
    shipped = json.loads((repo_root / "agents" / agent / SHIPPED[agent]).read_text())
    assert shipped == made
    # Every event the observer knows is there once, with one handler, and that handler is
    # the gate: the observer is never installed beside it, or each event is logged twice.
    assert set(made["hooks"]) == set(hooks_config(agent, "x")["hooks"])
    for groups in made["hooks"].values():
        ((handler,),) = [group["hooks"] for group in groups]
        words = handler.get("args") or handler["command"].split()
        assert "gate" in words and "--shadow" in words and "summarizer-hook" not in str(handler)
        assert "onFailure" not in handler  # a shadow gate stops nothing, by failing or otherwise


def test_the_codex_toml_example_says_what_its_json_says(repo_root):
    folder = repo_root / "agents" / "codex_code"
    toml = tomllib.loads((folder / "config.toml.example").read_text())
    assert toml == json.loads((folder / "hooks.json.example").read_text())


def test_only_the_fixture_folder_holds_the_deny_all_templates(repo_root):
    for agent in SHIPPED:
        folder = repo_root / "agents" / agent
        product = "".join(path.read_text() for path in folder.glob("*.example"))
        assert "agentarmor-monitor" in product and '/agentarmor" hook' not in product
        fixture = "".join(path.read_text() for path in (folder / "fixture").glob("*.example"))
        assert "/ABSOLUTE/PATH/agentarmor" in fixture and "agentarmor-monitor" not in fixture
    assert not (repo_root / "src" / "summarizer" / "agents").exists()
