"""CPU test fixtures; no model runtime or compiled executable required."""

import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root():
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def hook_cli(repo_root, tmp_path_factory):
    directory = tmp_path_factory.mktemp("AgentArmor install with spaces")
    command = directory / "agentarmor"
    # Match a console-script entry point, including its absolute interpreter.
    command.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"sys.path.insert(0, {str(repo_root / 'src/vllm')!r})\n"
        "from agentarmor_vllm.cli import main\nraise SystemExit(main())\n"
    )
    command.chmod(0o755)
    return command
