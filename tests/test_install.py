"""Exercise the Python-only bootstrap and platform-specific dependency plans."""

import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("bootstrap", Path(__file__).parents[1] / "install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def test_platform_selection_and_rejection():
    assert installer.choose_backend("auto", "Darwin", "arm64") == "metal"
    assert installer.choose_backend("auto", "Linux", "x86_64") == "cuda"
    assert installer.choose_backend("cpu", "Darwin", "x86_64") == "cpu"
    for backend, system, machine in (
        ("metal", "Linux", "aarch64"),
        ("cuda", "Darwin", "arm64"),
        ("auto", "Windows", "AMD64"),
    ):
        with pytest.raises(ValueError):
            installer.choose_backend(backend, system, machine)


def test_platform_dependency_plans_are_separate(tmp_path):
    uv, environment, root = tmp_path / "uv", tmp_path / "runtime", tmp_path
    cuda = installer.install_commands(uv, environment, root, "cuda")
    assert "vllm==0.32.0" in cuda[1]
    metal = installer.install_commands(uv, environment, root, "metal", ("core.whl", "metal.whl"))
    assert metal[1][-2:] == ["core.whl", "metal.whl"]
    assert "--override" in metal[1]
    assert not any("[vllm]" in value or "vllm==" in value for command in metal for value in command)
    cpu = installer.install_commands(uv, environment, root, "cpu")
    assert len(cpu) == 2  # Interpreter + API only; never pull a GPU runtime.
    assert cuda[0][cuda[0].index("--python") + 1] == "3.12"


def test_bootstrap_checks_download_before_executing(monkeypatch, tmp_path):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as wheel:
        wheel.writestr(f"uv-{installer.UV_VERSION}.data/scripts/uv", b"standalone-binary")
    content = archive.getvalue()
    metadata = {
        "urls": [
            {
                "filename": f"uv-{installer.UV_VERSION}-py3-none-macosx_11_0_arm64.whl",
                "url": "https://files.pythonhosted.org/test.whl",
                "digests": {"sha256": hashlib.sha256(content).hexdigest()},
            }
        ],
    }
    monkeypatch.setattr(installer.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(installer.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(installer, "fetch_json", lambda url: metadata)
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(content))
    destination = tmp_path / "bootstrap" / "uv"
    installer.bootstrap_uv(destination)
    assert destination.read_bytes() == b"standalone-binary"
    assert destination.stat().st_mode & 0o111
    destination.unlink()
    metadata["urls"][0]["digests"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="checksum"):
        installer.bootstrap_uv(destination)
    assert not destination.exists()
