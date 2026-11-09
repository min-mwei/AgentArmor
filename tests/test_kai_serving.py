"""CPU contracts for Kai's HF source, startup options and request admission."""

import sys
from types import SimpleNamespace

import pytest
from agentarmor_vllm.cache import KAI_REPO, kai_snapshot
from agentarmor_vllm.server import load_kai_service, parse_args
from agentarmor_vllm.wire import read_json
from test_vllm_baseline import request, service


@pytest.mark.parametrize("model", [None, "kai", "kai-4b", "Kai-4B"])
def test_kai_request_aliases_preserve_pointer_answers(model):
    req = request()
    if model is not None:
        req["model"] = model
    result = service().evaluate(req)
    assert result["model"] == "kai-4b"
    assert result["readout"] == "pointer"
    assert result["coverage"] == "complete"
    assert result["policy_ready"] is False
    assert set(result["answers"]["one"]["probabilities"]) == {"a", "z"}


@pytest.mark.parametrize("model", ["other-model", "Qwen/Qwen3.6-35B-A3B", ""])
def test_foreign_model_is_rejected_before_inference(model):
    candidate = service()
    req = request()
    req["model"] = model
    with pytest.raises(ValueError, match="resident model"):
        candidate.evaluate(req)
    assert not candidate.backbone.rows


@pytest.mark.parametrize("change", ["unknown_field", "bad_criteria", "surrogate", "nan"])
def test_invalid_request_is_rejected_before_inference(change):
    candidate = service()
    req = request()
    if change == "unknown_field":
        req["questions"]["two"]["ignore"] = True
    elif change == "bad_criteria":
        req["questions"]["two"]["criteria"] = {"hidden": "cannot be silently ignored"}
    elif change == "surrogate":
        req["state"] = "\ud800"
    else:
        req["state"] = float("nan")
    with pytest.raises(ValueError):
        candidate.evaluate(req)
    assert not candidate.backbone.rows


@pytest.mark.parametrize("payload", [b'"\\ud800"', b'{"\\udfff":1}'])
def test_wire_rejects_unencodable_unicode(payload):
    with pytest.raises(UnicodeError):
        read_json(payload)


def test_kai_hf_source_requires_a_pinned_export(monkeypatch, tmp_path):
    revision = "a" * 40
    root = tmp_path / "snapshots" / revision
    root.mkdir(parents=True)
    calls = []

    def download(repo, **kwargs):
        calls.append((repo, kwargs))
        return str(root)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    with pytest.raises(ValueError, match="no trainer-published"):
        kai_snapshot(revision)
    (root / "vllm").mkdir()
    (root / "vllm/kai.json").write_text("{}")
    assert kai_snapshot(revision, "/cache", True) == (root / "vllm", tmp_path / "blobs")
    assert calls[-1] == (
        KAI_REPO,
        {
            "revision": revision,
            "allow_patterns": ["vllm/*"],
            "cache_dir": "/cache",
            "local_files_only": True,
            "token": False,
        },
    )
    with pytest.raises(ValueError, match="unexpected Hugging Face snapshot"):
        kai_snapshot("b" * 40)
    for invalid in ("main", "", None, "a" * 39, "a" * 41):
        before = len(calls)
        with pytest.raises(ValueError, match="full HF commit"):
            kai_snapshot(invalid)
        assert len(calls) == before


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--model", "qwen3.6-35b-a3b", "--package", "x"],
        ["--kai-revision", "main"],
        ["--kai-revision", ""],
        ["--package", ""],
        ["--package", "x", "--kai-revision", "a" * 40],
        ["--package", "x", "--cache-dir", "/cache"],
        ["--package", "x", "--local-files-only"],
        ["--package", "x", "--max-model-len", "8192"],
        ["--package", "x", "--tensor-parallel-size", "2"],
        ["--package", "x", "--port", "0"],
        ["--package", "x", "--port", "65536"],
    ],
)
def test_startup_rejects_incompatible_options(argv):
    with pytest.raises(SystemExit) as error:
        parse_args(argv)
    assert error.value.code == 2


def test_local_package_does_not_access_hf(monkeypatch):
    import agentarmor_vllm.cache as cache
    import agentarmor_vllm.engine as engine

    monkeypatch.setattr(cache, "kai_snapshot", lambda *_: pytest.fail("unexpected HF request"))
    monkeypatch.setattr(engine, "load_service", lambda package: ("kai", package))
    assert load_kai_service(parse_args(["--package", "synthetic"])) == ("kai", "synthetic")


def test_hf_loader_preserves_source_identity_and_cache_boundary(monkeypatch, tmp_path):
    import agentarmor_vllm.cache as cache
    import agentarmor_vllm.engine as engine

    revision = "a" * 40
    package, blobs = tmp_path / "vllm", tmp_path / "blobs"
    captured = {}

    def snapshot(*args):
        captured["snapshot"] = args
        return package, blobs

    def load(directory, **kwargs):
        captured["load"] = (directory, kwargs)
        return "loaded"

    monkeypatch.setattr(cache, "kai_snapshot", snapshot)
    monkeypatch.setattr(engine, "load_service", load)
    args = parse_args(
        [
            "--model",
            "kai-4b",
            "--kai-revision",
            revision,
            "--cache-dir",
            "/cache",
            "--local-files-only",
        ]
    )
    assert load_kai_service(args) == "loaded"
    assert captured == {
        "snapshot": (revision, "/cache", True),
        "load": (
            package,
            {"hf_blobs": blobs, "source": {"hf_repo": KAI_REPO, "revision": revision}},
        ),
    }

    def unavailable(*_):
        raise FileNotFoundError("missing pinned export")

    monkeypatch.setattr(cache, "kai_snapshot", unavailable)
    monkeypatch.setattr(engine, "load_service", lambda *_: pytest.fail("must not fall back"))
    with pytest.raises(FileNotFoundError, match="missing pinned export"):
        load_kai_service(args)
