"""Developer-only checks of the compiled native executable."""

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root():
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def native_binary(repo_root, tmp_path_factory):
    source = Path(
        os.environ.get(
            "AGENTARMOR_TEST_BINARY", repo_root / "build/release/native/kai/server/agentarmor"
        )
    ).resolve()
    if not source.is_file():
        pytest.fail("Build agentarmor first or set AGENTARMOR_TEST_BINARY to the native executable")
    directory = tmp_path_factory.mktemp("AgentArmor install with spaces")
    binary = directory / "agentarmor"
    shutil.copy2(source, binary)
    return binary


@pytest.fixture
def no_python_environment(tmp_path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    return {"PATH": str(empty), "PYTHONHOME": str(tmp_path / "missing-python")}
