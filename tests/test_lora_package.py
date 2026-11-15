"""CPU contracts for an unmerged package (schema agentarmor-qwen-lora/v1): what is verified
before anything is allocated, what the service says it is, and which backbone answers it.

Everything is synthetic: a made-up base directory, adapter and runtime. No MLX, weights or network.
"""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from agentarmor_vllm import engine, server
from agentarmor_vllm import package as packages
from agentarmor_vllm.encode import tokenizer_sha256
from agentarmor_vllm.package import (
    BASE,
    BASE_REVISION,
    LORA_SCHEMA,
    LoraPackage,
    adapter_layout,
    sha256,
)
from safetensors.numpy import save_file
from test_mlx_backbone import export, head, never  # noqa: F401 - export is a fixture
from test_vllm_baseline import Tokenizer

VERSIONS = {"mlx": "0.32.4", "mlx-metal": "0.32.4", "mlx-lm": "0.32.2"}
LORA = {"rank": 4, "alpha": 8, "targets": ["q_proj", "in_proj_a"]}
Q, A = "layers.0.self_attn.q_proj", "layers.1.linear_attn.in_proj_a"


def factors(names=(Q, A), rank=4, inputs=8, outputs=6, dtype=np.float32):
    tensors = {}
    for name in names:
        tensors[f"base_model.model.{name}.lora_A.weight"] = np.ones((rank, inputs), dtype=dtype)
        tensors[f"base_model.model.{name}.lora_B.weight"] = np.ones((outputs, rank), dtype=dtype)
    return tensors


class Made:
    """A synthetic package, its base directory and the runtime this interpreter is said to have."""

    def __init__(self, tmp_path, monkeypatch):
        self.root, self.base, runtime = tmp_path / "package", tmp_path / "base", tmp_path / "rt"
        for folder in (self.root, self.base, runtime):
            folder.mkdir()
        text = {"model_type": "qwen3_5_text", "hidden_size": 2560, "num_hidden_layers": 32}
        self.config = {"model_type": "qwen3_5", "text_config": text}
        (self.base / "config.json").write_text(json.dumps(self.config))
        (self.base / "tokenizer.json").write_text("{}")
        (self.base / "tokenizer_config.json").write_text("{}")
        shard = "model.safetensors-00001-of-00001.safetensors"
        (self.base / shard).write_bytes(b"synthetic base weights, not loadable")
        index = {"weight_map": {"embed_tokens.weight": shard}}
        (self.base / "model.safetensors.index.json").write_text(json.dumps(index))
        self.shard = self.base / shard
        (runtime / "qwen3_5.py").write_text("# the text body\n")
        (runtime / "gated_delta.py").write_text("# its update\n")
        shutil.copyfile(Path(packages.__file__).with_name("delta.py"), runtime / "delta.py")
        self.installed = {
            "versions": dict(VERSIONS),
            "sources": {name: runtime / name for name in ("qwen3_5.py", "gated_delta.py")},
            "delta": runtime / "delta.py",
        }
        monkeypatch.setattr(packages, "installed_runtime", lambda: self.installed)
        save_file(factors(), self.root / "adapter_model.safetensors")
        save_file(head(), self.root / "pointer.safetensors")
        self.meta = {
            "schema": LORA_SCHEMA,
            "base": BASE,
            "base_revision": BASE_REVISION,
            "base_files": {p.name: sha256(p) for p in self.base.iterdir()},
            "weights": "lora-unmerged",
            "dtype": "bfloat16",
            "lora": dict(LORA),
            "files": {
                name: sha256(self.root / name)
                for name in ("adapter_model.safetensors", "pointer.safetensors")
            },
            "temperature": 1.0,
            "max_state": 4096,
            "max_row": 4742,
            "max_packed": 5760,
            "pauses": 0,
            "pause_id": None,
            "tokenizer_sha256": tokenizer_sha256(Tokenizer()),
            "evidence": "unavailable",
            "runtime": {
                **VERSIONS,
                "text_body": "train",
                "delta": {"impl": "agentarmor_vllm.delta", "sha256": sha256(runtime / "delta.py")},
                "mlx_lm_sources": {
                    name: sha256(path) for name, path in self.installed["sources"].items()
                },
            },
        }
        self.write()

    def write(self):
        (self.root / "kai.json").write_text(json.dumps(self.meta))

    def adapter(self, tensors):
        """Another adapter file, bound by the manifest: only its layout can then be at fault."""
        save_file(tensors, self.root / "adapter_model.safetensors")
        self.meta["files"]["adapter_model.safetensors"] = sha256(
            self.root / "adapter_model.safetensors"
        )
        self.write()

    def rebase(self, config):
        (self.base / "config.json").write_text(json.dumps(config))
        self.meta["base_files"]["config.json"] = sha256(self.base / "config.json")
        self.write()


@pytest.fixture
def made(tmp_path_factory, monkeypatch):
    return Made(tmp_path_factory.mktemp("unmerged"), monkeypatch)


def append(path):
    with path.open("ab") as stream:
        stream.write(b"\0")


def changed_adapter(m):
    append(m.root / "adapter_model.safetensors")


def changed_pointer(m):
    append(m.root / "pointer.safetensors")


def changed_shard(m):
    append(m.shard)


def changed_tokenizer_file(m):
    append(m.base / "tokenizer.json")


def unbound_base_file(m):
    # mlx-lm would read it: a second weight file, or a side file that turns quantization on.
    (m.base / "model-extra.safetensors").write_bytes(b"more weights")


def unbound_side_file(m):
    (m.base / "hf_quant_config.json").write_text("{}")


def missing_base_file(m):
    (m.base / "tokenizer_config.json").unlink()


def unbound_package_file(m):
    (m.root / "notes.txt").write_text("unbound")


def shards_not_the_index(m):
    (m.base / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "b"}}))
    m.meta["base_files"]["model.safetensors.index.json"] = sha256(
        m.base / "model.safetensors.index.json"
    )
    m.write()


def other_mlx_lm(m):
    m.installed["versions"]["mlx-lm"] = "0.31.3"


def other_mlx(m):
    m.installed["versions"]["mlx"] = "0.31.2"


def no_mlx_metal(m):
    m.installed["versions"]["mlx-metal"] = None


def changed_delta(m):
    append(m.installed["delta"])


def changed_text_body_source(m):
    append(m.installed["sources"]["qwen3_5.py"])


def changed_update_source(m):
    append(m.installed["sources"]["gated_delta.py"])


def no_mlx_lm_source(m):
    m.installed["sources"]["qwen3_5.py"] = None


def manifest(**changes):
    def fault(m):
        for key, value in changes.items():
            if value is None:
                del m.meta[key]
            else:
                m.meta[key] = value
        m.write()

    return fault


def runtime(**changes):
    def fault(m):
        m.meta["runtime"] |= changes
        m.write()

    return fault


def adapter(**shape):
    return lambda m: m.adapter(factors(**shape))


def extra_tensor(m):
    m.adapter(factors() | {"base_model.model.layers.0.extra": np.ones((2, 2), dtype=np.float32)})


def unpaired_factor(m):
    tensors = factors()
    del tensors[f"base_model.model.{A}.lora_B.weight"]
    m.adapter(tensors)


def model_file(m):
    m.rebase(m.config | {"model_file": "model.py"})


def text_model_file(m):
    m.rebase(m.config | {"text_config": m.config["text_config"] | {"model_file": "model.py"}})


def quantization(m):
    m.rebase(m.config | {"quantization": {"group_size": 64, "bits": 4}})


def text_quantization(m):
    text = m.config["text_config"] | {"quantization_config": {"bits": 4}}
    m.rebase(m.config | {"text_config": text})


def other_backbone(m):
    m.rebase(m.config | {"text_config": m.config["text_config"] | {"num_hidden_layers": 24}})


FAULTS = [
    changed_adapter,
    changed_pointer,
    changed_shard,
    changed_tokenizer_file,
    unbound_base_file,
    unbound_side_file,
    missing_base_file,
    unbound_package_file,
    shards_not_the_index,
    other_mlx_lm,
    other_mlx,
    no_mlx_metal,
    changed_delta,
    changed_text_body_source,
    changed_update_source,
    no_mlx_lm_source,
    manifest(schema="agentarmor-qwen/v1"),
    manifest(weights="merged"),
    manifest(dtype="float16"),
    manifest(evidence="frozen-from-pointer"),
    manifest(base_revision="0" * 40),
    manifest(base_files=None),
    manifest(runtime=None),
    manifest(extra="field"),
    manifest(lora={"rank": 4, "alpha": 8}),
    manifest(lora={"rank": 0, "alpha": 8, "targets": ["q_proj"]}),
    manifest(lora={"rank": 4, "alpha": 8, "targets": ["q_proj", "q_proj"]}),
    manifest(max_row=4000),
    runtime(text_body="eval"),
    runtime(delta={"impl": "another.module", "sha256": "0" * 64}),
    runtime(delta="0" * 64),
    runtime(mlx_lm_sources={"qwen3_5.py": "0" * 64}),
    runtime(more="x"),
    adapter(rank=8),  # not the package's rank
    adapter(names=(Q, "layers.0.mlp.down_proj")),  # not a target projection
    adapter(names=(Q, "elsewhere.0.q_proj")),  # not in a decoder layer
    adapter(dtype=np.float16),
    extra_tensor,
    unpaired_factor,
    model_file,
    text_model_file,
    quantization,
    text_quantization,
    other_backbone,
]


def ids(fault):
    return getattr(fault, "__name__", None)


def test_a_whole_package_is_accepted_and_named_by_its_manifest(made):
    package = LoraPackage(made.root, base_dir=made.base)
    assert package.identity == sha256(made.root / "kai.json")
    assert package.weights == "lora-unmerged" and package.hidden_size == 2560
    assert package.model_dir == made.base.resolve()
    # The adapter's projections and their dimensions, known from the file's header alone.
    assert package.adapter == {f"language_model.model.{name}": (8, 6) for name in (Q, A)}
    assert package.runtime is made.installed


@pytest.mark.parametrize("fault", FAULTS, ids=ids)
def test_anything_unbound_changed_or_not_installed_is_refused(made, monkeypatch, fault):
    fault(made)
    with pytest.raises(ValueError):
        LoraPackage(made.root, base_dir=made.base)
    # ... and before a tokenizer, a pointer or a backbone exists.
    opened = stub_tokenizer(monkeypatch, made.base)
    monkeypatch.setattr(engine, "MlxLoraBackbone", never)
    monkeypatch.setattr(engine, "MlxBackbone", never)
    with pytest.raises(ValueError):
        engine.load_service(made.root, backend="mlx", base_dir=made.base)
    assert not opened


def test_adapter_layout_reads_no_tensor(tmp_path):
    path = tmp_path / "adapter.safetensors"
    save_file(factors(), path)
    whole = adapter_layout(path, LORA)
    size = 8 + int.from_bytes(path.read_bytes()[:8], "little")
    path.write_bytes(path.read_bytes()[:size])  # the header alone
    assert adapter_layout(path, LORA) == whole
    path.write_bytes(path.read_bytes()[: size - 1])
    with pytest.raises(ValueError):
        adapter_layout(path, LORA)


def test_the_base_is_found_in_the_local_cache_only(made, monkeypatch):
    import sys
    from types import SimpleNamespace

    asked = []

    def snapshot_download(repo, **kwargs):
        asked.append((repo, kwargs))
        if kwargs["cache_dir"] == "absent":
            raise FileNotFoundError("not cached")
        return str(made.base)

    hub = SimpleNamespace(snapshot_download=snapshot_download)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    assert LoraPackage(made.root).model_dir == made.base.resolve()
    offline = {"revision": BASE_REVISION, "local_files_only": True, "token": False}
    assert asked == [(BASE, offline | {"cache_dir": None})]
    with pytest.raises(ValueError, match="--base-dir"):
        LoraPackage(made.root, cache_dir="absent")


def stub_tokenizer(monkeypatch, directory):
    import sys
    from types import SimpleNamespace

    opened = []

    def from_pretrained(path, **kwargs):
        assert path == directory.resolve()
        assert kwargs == {"local_files_only": True, "trust_remote_code": False}
        opened.append(path)
        return Tokenizer()

    auto = SimpleNamespace(from_pretrained=from_pretrained)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoTokenizer=auto))
    return opened


class Sentinel:
    def __init__(self, package):
        self.package = package


def test_the_schema_picks_the_backbone_and_the_service_says_what_it_is(
    made,
    export,  # noqa: F811
    monkeypatch,
):
    class Lora(Sentinel):
        name = "mlx-lora"

    class Merged(Sentinel):
        name = "mlx"

    monkeypatch.setattr(engine, "MlxLoraBackbone", Lora)
    monkeypatch.setattr(engine, "MlxBackbone", Merged)
    monkeypatch.setattr(engine, "VllmBackbone", never)
    opened = stub_tokenizer(monkeypatch, made.base)
    lora = engine.load_service(made.root, backend="mlx", base_dir=made.base)
    assert type(lora.backbone) is Lora and opened == [made.base.resolve()]
    assert lora.metadata() == {
        "backend": "mlx-lora",
        "model": "kai-4b",
        "readout": "pointer",
        "policy_ready": False,
        "evidence_available": False,
        "package_sha256": sha256(made.root / "kai.json"),
        "diagnostic": True,
        "weights": "lora-unmerged",
    }
    # A merged export in the same process: its own backbone, and it claims no weights form.
    stub_tokenizer(monkeypatch, export / "model")
    merged = engine.load_service(export, backend="mlx", base_dir=made.base)
    assert type(merged.backbone) is Merged
    assert merged.metadata()["backend"] == "mlx" and "weights" not in merged.metadata()
    assert merged.metadata()["package_sha256"] != lora.metadata()["package_sha256"]


@pytest.mark.parametrize("how", ["vllm", "hf_blobs"])
def test_an_unmerged_package_is_local_and_mlx_only(made, monkeypatch, how):
    opened = stub_tokenizer(monkeypatch, made.base)
    for name in ("MlxLoraBackbone", "MlxBackbone", "VllmBackbone"):
        monkeypatch.setattr(engine, name, never)
    monkeypatch.setattr(packages, "LoraPackage", never)  # refused before the base is hashed
    options = {"backend": "vllm"} if how == "vllm" else {"backend": "mlx", "hf_blobs": made.base}
    with pytest.raises(ValueError, match="MLX backend"):
        engine.load_service(made.root, base_dir=made.base, **options)
    assert not opened


def test_verification_alone_builds_no_backbone(made, monkeypatch):
    opened = stub_tokenizer(monkeypatch, made.base)
    monkeypatch.setattr(engine, "MlxLoraBackbone", never)
    service = engine.load_service(made.root, backend="mlx", base_dir=made.base, allocate=False)
    assert service.backbone is None and len(opened) == 1
    assert service.package.identity == sha256(made.root / "kai.json")


def test_the_server_takes_either_schema_and_can_verify_without_serving(
    made,
    export,  # noqa: F811
    monkeypatch,
    capsys,
):
    args = server.parse_args(["--package", "p", "--backend", "mlx", "--base-dir", "b"])
    assert (args.base_dir, args.verify_only) == ("b", False)
    with pytest.raises(SystemExit):  # a published export carries its own weights
        server.parse_args(["--kai-revision", "0" * 40, "--base-dir", "b"])
    for name in ("MlxLoraBackbone", "MlxBackbone", "VllmBackbone"):
        monkeypatch.setattr(engine, name, never)
    monkeypatch.setattr(server, "HTTPServer", never)

    def from_pretrained(path, **_):
        assert path in (made.base.resolve(), export / "model")
        return Tokenizer()

    import sys
    from types import SimpleNamespace

    auto = SimpleNamespace(from_pretrained=from_pretrained)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoTokenizer=auto))
    # The default checkpoint merged, the session checkpoint unmerged: one server, two schemas.
    common = ["--backend", "mlx", "--base-dir", str(made.base), "--verify-only"]
    server.main(["--package", str(export), "--session-package", str(made.root), *common])
    default, session = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert (default["role"], default["schema"], default["weights"]) == (
        "default",
        "agentarmor-qwen/v1",
        "merged",
    )
    assert (session["role"], session["schema"], session["weights"]) == (
        "session",
        LORA_SCHEMA,
        "lora-unmerged",
    )
    assert default["package_sha256"] == sha256(export / "kai.json")
    assert session["package_sha256"] == sha256(made.root / "kai.json")
    # A refused package verifies nothing.
    changed_shard(made)
    with pytest.raises(ValueError, match="base file hash mismatch"):
        server.main(["--package", str(made.root), *common])
    assert capsys.readouterr().out == ""
