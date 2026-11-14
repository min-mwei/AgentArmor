"""CPU contracts for the local MLX backbone. No MLX runtime, weights or network are used."""

import http.client
import json
import sys
import threading
from http.server import HTTPServer
from types import SimpleNamespace

import numpy as np
import pytest
from agentarmor_vllm.encode import tokenizer_sha256
from agentarmor_vllm.engine import MlxBackbone, VllmBackbone
from agentarmor_vllm.server import handler
from test_vllm_baseline import Backbone, Tokenizer, request, service

# numpy in the place of mlx.core: only the calls MlxBackbone.hidden makes.
ARRAYS = SimpleNamespace(array=np.array, int32=np.int32, float32=np.float32)
PACKAGE = SimpleNamespace(hidden_size=2, meta={"max_row": 8})


class Body:
    """Stands in for mlx-lm's Qwen3.5 text body and records every forward it runs."""

    def __init__(self, out=None):
        self.calls, self.out = [], out

    def __call__(self, inputs, cache=None):
        self.calls.append((inputs.dtype, inputs.tolist(), cache))
        if self.out is not None:
            return self.out
        # Causal: position t holds the running sum of the row's ids and t, in a wider dtype.
        sums = np.cumsum(inputs, axis=-1, dtype=np.float64)
        return np.stack([sums, np.broadcast_to(np.arange(inputs.shape[-1]), sums.shape)], axis=-1)


class MlxStandIn(Backbone):
    name = "mlx"


def test_service_and_health_name_the_backbone_in_use():
    assert (VllmBackbone.name, MlxBackbone.name) == ("vllm", "mlx")
    vllm, mlx = service(), service()
    mlx.backbone = MlxStandIn()
    for s, name in ((vllm, "vllm"), (mlx, "mlx")):
        assert s.metadata()["backend"] == name
        result = s.evaluate(request())
        assert result["backend"] == name and result["coverage"] == "complete"
    assert len(mlx.backbone.rows) == 2  # both answers came from the MLX-named backbone
    with HTTPServer(("127.0.0.1", 0), handler(mlx)) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/health")
            assert json.loads(connection.getresponse().read())["backend"] == "mlx"
            connection.close()
        finally:
            server.shutdown()
            thread.join()


def test_hidden_runs_one_isolated_row_per_call():
    body = Body()
    backbone = MlxBackbone(PACKAGE, text=body, mx=ARRAYS)
    a, b = [7, 8, 9], [1, 2]
    first, second = backbone.hidden({"ids": a}), backbone.hidden({"ids": b})
    # One forward per row: a batch of one holding exactly that row's tokens, and no cache.
    assert body.calls == [(np.int32, [a], None), (np.int32, [b], None)]
    assert first.dtype == np.float32 and first.shape == (3, 2)
    np.testing.assert_array_equal(first, [[7, 0], [15, 1], [24, 2]])
    # Nothing carries over: the second row reads the same after the first as on its own.
    alone = MlxBackbone(PACKAGE, text=Body(), mx=ARRAYS).hidden({"ids": b})
    np.testing.assert_array_equal(second, alone)


@pytest.mark.parametrize(
    "out",
    [
        np.zeros((1, 2, 2)),  # a position short
        np.zeros((1, 4, 2)),  # a position extra
        np.zeros((1, 3, 3)),  # another width
        np.zeros((2, 3, 2)),  # two rows for one
        np.zeros((3, 2)),  # no batch axis
        np.full((1, 3, 2), np.nan),
        np.array([[[0, 0], [0, np.inf], [0, 0]]]),
    ],
)
def test_incomplete_or_nonfinite_states_are_refused(out):
    body = Body(out)
    with pytest.raises(ValueError):
        MlxBackbone(PACKAGE, text=body, mx=ARRAYS).hidden({"ids": [1, 2, 3]})
    assert len(body.calls) == 1


def test_row_beyond_the_checkpoint_limit_is_refused_before_a_forward():
    body = Body()
    backbone = MlxBackbone(PACKAGE, text=body, mx=ARRAYS)
    assert backbone.hidden({"ids": [1] * 8}).shape == (8, 2)
    with pytest.raises(ValueError, match="row limit"):
        backbone.hidden({"ids": [1] * 9})
    assert len(body.calls) == 1


@pytest.mark.parametrize(
    "fault", [None, "float16", "float32", "uint32", "model_file", "text_model_file", "quantization"]
)
def test_mlx_loads_the_local_export_in_bf16_only(monkeypatch, tmp_path, fault):
    config = {"model_type": "qwen3_5", "text_config": {"model_type": "qwen3_5_text"}}
    if fault == "model_file":
        config["model_file"] = "model.py"
    elif fault == "text_model_file":
        config["text_config"]["model_file"] = "model.py"
    elif fault == "quantization":
        config["quantization"] = {"group_size": 64, "bits": 4}
    (tmp_path / "model").mkdir()
    (tmp_path / "model/config.json").write_text(json.dumps(config))
    weights = {"embed_tokens.weight": "bfloat16", "norm.weight": "bfloat16"}
    if fault in ("float16", "float32", "uint32"):
        weights["layers.0.mlp.down_proj.weight"] = fault
    body, steps = Body(), []

    class LM:
        language_model = SimpleNamespace(model=body)

        def eval(self):
            steps.append("eval")

        def parameters(self):
            return {key: SimpleNamespace(dtype=dtype) for key, dtype in weights.items()}

    def load_model(path, **kwargs):
        steps.append(("load", path, kwargs))
        return LM(), config

    core = SimpleNamespace(**vars(ARRAYS), bfloat16="bfloat16")
    utils = SimpleNamespace(tree_flatten=lambda tree: list(tree.items()))
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core, utils=utils))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setitem(sys.modules, "mlx.utils", utils)
    monkeypatch.setitem(sys.modules, "mlx_lm", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "mlx_lm.utils", SimpleNamespace(load_model=load_model))
    package = SimpleNamespace(root=tmp_path, hidden_size=2, meta={"max_row": 8})
    if fault in ("model_file", "text_model_file", "quantization"):
        with pytest.raises(ValueError, match="model code or a quantization"):
            MlxBackbone(package)
        assert not steps  # refused before mlx-lm reads or imports anything
        return
    loaded = [("load", tmp_path / "model", {}), "eval"]
    if fault:
        with pytest.raises(ValueError, match="bfloat16"):
            MlxBackbone(package)
        assert steps == loaded
        return
    backbone = MlxBackbone(package)
    assert steps == loaded
    np.testing.assert_array_equal(backbone.hidden({"ids": [3, 4]}), [[3, 0], [7, 1]])
    assert body.calls == [(np.int32, [[3, 4]], None)]


def head(width=2560):
    return {
        "q.weight": np.zeros((2, width), dtype=np.float32),
        "q.bias": np.ones(2, dtype=np.float32),
        "k.weight": np.zeros((2, width), dtype=np.float32),
        "k.bias": np.ones(2, dtype=np.float32),
    }


@pytest.fixture
def export(tmp_path):
    """A synthetic export whose manifest, tokenizer and pointer pass; its weights cannot load."""
    from agentarmor_vllm.package import BASE_REVISION, sha256
    from safetensors.numpy import save_file

    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text(
        json.dumps({"model_type": "qwen3_5_text", "hidden_size": 2560, "num_hidden_layers": 32})
    )
    (model / "tokenizer.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"synthetic weights, not loadable")
    save_file(head(), tmp_path / "pointer.safetensors")
    meta = {
        "schema": "agentarmor-qwen/v1",
        "base": "Qwen/Qwen3.5-4B-Base",
        "base_revision": BASE_REVISION,
        "weights": "merged",
        "dtype": "bfloat16",
        "evidence": "frozen-from-pointer",
        "temperature": 1.0,
        "max_state": 384,
        "max_row": 1024,
        "max_packed": 2048,
        "pauses": 0,
        "pause_id": None,
        "tokenizer_sha256": tokenizer_sha256(Tokenizer()),
        "files": {
            p.relative_to(tmp_path).as_posix(): sha256(p)
            for p in tmp_path.rglob("*")
            if p.is_file()
        },
    }
    (tmp_path / "kai.json").write_text(json.dumps(meta))
    return tmp_path


def stub_tokenizer(monkeypatch, root):
    opened = []

    def from_pretrained(path, **kwargs):
        assert path == root / "model"
        assert kwargs == {"local_files_only": True, "trust_remote_code": False}
        opened.append(path)
        return Tokenizer()

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=from_pretrained)),
    )
    return opened


def never(*_, **__):
    pytest.fail("a backbone was built")


@pytest.mark.parametrize("backend", ["nonsense", "", "MLX", "metal", None])
def test_unknown_backend_is_refused_before_the_export_is_read(export, monkeypatch, backend):
    import agentarmor_vllm.engine as engine

    opened = stub_tokenizer(monkeypatch, export)
    monkeypatch.setattr(engine, "VllmBackbone", never)
    monkeypatch.setattr(engine, "MlxBackbone", never)
    with pytest.raises(ValueError, match="unknown serving backend"):
        engine.load_service(export, backend=backend)
    assert not opened


@pytest.mark.parametrize("fault", [None, "manifest", "tokenizer", "head"])
def test_mlx_backbone_is_built_only_after_the_export_passes(export, monkeypatch, fault):
    import agentarmor_vllm.engine as engine
    from agentarmor_vllm.package import sha256
    from safetensors.numpy import save_file

    meta = json.loads((export / "kai.json").read_text())
    if fault == "manifest":
        meta["files"]["pointer.safetensors"] = "0" * 64
    elif fault == "tokenizer":
        meta["tokenizer_sha256"] = "0" * 64
    elif fault == "head":
        save_file(head(width=4), export / "pointer.safetensors")
        meta["files"]["pointer.safetensors"] = sha256(export / "pointer.safetensors")
    (export / "kai.json").write_text(json.dumps(meta))
    built = []

    class Sentinel:
        name = "mlx"

        def __init__(self, package):
            if fault:
                pytest.fail("MLX backbone built for an export that failed validation")
            built.append(package.identity)

    stub_tokenizer(monkeypatch, export)
    monkeypatch.setattr(engine, "VllmBackbone", never)
    monkeypatch.setattr(engine, "MlxBackbone", Sentinel)
    if fault:
        with pytest.raises(ValueError):
            engine.load_service(export, backend="mlx")
        assert not built
        return
    loaded = engine.load_service(export, backend="mlx")
    assert built == [sha256(export / "kai.json")]
    assert type(loaded.backbone) is Sentinel
    assert loaded.metadata()["backend"] == "mlx"
