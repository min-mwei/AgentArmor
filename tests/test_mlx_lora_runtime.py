"""The unmerged MLX runtime on the CPU device: the chunked DeltaNet update against mlx-lm's own,
the adapter wiring, and the one substitution an unmerged backbone makes.

Needs the serving environment's MLX and mlx-lm (pyproject extra serve-mlx); skipped without
them. Tiny random tensors and a toy text body only: no model weights, no GPU, no network.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

mx = pytest.importorskip("mlx.core")
pytest.importorskip("mlx_lm")

import mlx.nn as nn  # noqa: E402
import mlx_lm.utils  # noqa: E402
from agentarmor_vllm import delta, engine  # noqa: E402
from agentarmor_vllm.package import adapter_layout, installed_runtime  # noqa: E402
from mlx_lm.models import gated_delta as reference  # noqa: E402
from mlx_lm.models import qwen3_5  # noqa: E402
from mlx_lm.tuner.lora import LoRALinear  # noqa: E402

LORA = {"rank": 4, "alpha": 8, "targets": ["q_proj", "in_proj_a"]}
WIDTH = 8


@pytest.fixture(autouse=True)
def cpu():
    before = mx.default_device()
    mx.set_default_device(mx.cpu)
    yield
    mx.set_default_device(before)


def inputs(seed, tokens, heads_k=2, heads_v=2, dk=8, dv=6, batch=1, dtype=None):
    """Random inputs in the shapes and conventions the text body calls the update with."""
    rng = np.random.default_rng(seed)

    def normal(*shape):
        return mx.array(rng.standard_normal(shape).astype(np.float32))

    q, k = normal(batch, tokens, heads_k, dk), normal(batch, tokens, heads_k, dk)
    q, k = reference.normalize_qk(q, k, inv_scale=dk**-0.5, eps=1e-6)
    v = normal(batch, tokens, heads_v, dv)
    a, b = normal(batch, tokens, heads_v), normal(batch, tokens, heads_v)
    a_log, dt_bias = normal(heads_v) * 0.5, normal(heads_v) * 0.5
    state = normal(batch, heads_v, dv, dk) * 0.3
    if dtype is not None:
        q, k, v, a, b = (x.astype(dtype) for x in (q, k, v, a, b))
    return (q, k, v, a, b, a_log, dt_bias), state


@pytest.mark.parametrize("tokens", [1, 37, 63, 64, 65, 100, 128, 130, 192])
@pytest.mark.parametrize("with_state", [False, True])
@pytest.mark.parametrize("heads", [(2, 2), (1, 2)])
def test_chunked_update_equals_mlx_lm_token_by_token_update(tokens, with_state, heads):
    # Whole chunks (64, 128, 192), a padded last chunk, and fewer tokens than one chunk; with
    # the fresh state a whole row starts from and with a carried one; with shared key heads.
    args, state = inputs(tokens * 7 + with_state, tokens, *heads, batch=2)
    state = state if with_state else None
    ours, ours_state = delta.gated_delta_update(*args, state, None, use_kernel=False)
    theirs, their_state = reference.gated_delta_update(*args, state, None, use_kernel=False)
    assert ours.shape == theirs.shape and ours_state.shape == their_state.shape
    assert ours.dtype == theirs.dtype == mx.float32 and ours_state.dtype == mx.float32
    np.testing.assert_allclose(np.array(ours), np.array(theirs), rtol=2e-4, atol=2e-5)
    np.testing.assert_allclose(np.array(ours_state), np.array(their_state), rtol=2e-4, atol=2e-5)


def test_chunked_update_keeps_the_callers_number_type_and_computes_in_fp32():
    args, _ = inputs(5, 70, dtype=mx.bfloat16)
    ours, state = delta.gated_delta_update(*args, None, None, use_kernel=False)
    assert ours.dtype == mx.bfloat16 and state.dtype == mx.float32
    exact = tuple(x.astype(mx.float32) for x in args)
    theirs, _ = reference.gated_delta_update(*exact, None, None, use_kernel=False)
    # The same BF16 values, updated in FP32 and rounded once to BF16 on the way out.
    np.testing.assert_allclose(
        np.array(ours.astype(mx.float32)), np.array(theirs), rtol=1e-2, atol=1e-2
    )


def test_the_checkpoint_wrapper_changes_no_forward_value():
    args, state = inputs(11, 90)
    q, k, v, a, b, a_log, dt_bias = args
    beta = mx.sigmoid(b)
    log_g = -mx.exp(a_log.astype(mx.float32)) * nn.softplus((a + dt_bias).astype(mx.float32))
    for carried in (None, state):
        plain = delta.chunked_gated_delta(q, k, v, log_g, beta, carried)
        wrapped = delta.gated_delta_update(*args, carried, None, use_kernel=False)
        for left, right in zip(plain, wrapped, strict=True):
            np.testing.assert_array_equal(np.array(left), np.array(right))


@pytest.mark.parametrize("call", ["kernel", "mask", "option"])
def test_anything_but_a_training_mode_whole_row_goes_to_mlx_lm_unchanged(monkeypatch, call):
    args, state = inputs(3, 4)
    seen = []

    def theirs(*positional, **named):
        seen.append((positional, named))
        return "mlx-lm's answer"

    monkeypatch.setattr(reference, "gated_delta_update", theirs)
    monkeypatch.setattr(delta, "chunked_gated_delta", engine_never)
    mask = mx.ones((1, 4)) if call == "mask" else None
    named = {"use_kernel": call == "kernel"} | ({"lower_bound": -1.0} if call == "option" else {})
    assert delta.gated_delta_update(*args, state, mask, **named) == "mlx-lm's answer"
    ((positional, passed),) = seen
    assert len(positional) == 9 and positional[7] is state and positional[8] is mask
    assert passed == named  # use_kernel by keyword, as mlx-lm 0.32.2 requires


def engine_never(*_, **__):
    pytest.fail("the chunked update ran")


# --- the adapter on a toy model ---


class Attention(nn.Module):
    def __init__(self):
        super().__init__()
        self.q_proj = nn.Linear(WIDTH, WIDTH, bias=False)
        self.o_proj = nn.Linear(WIDTH, WIDTH, bias=False)  # not a target here: stays as it is


class DeltaNet(nn.Module):
    """Calls the update exactly as mlx-lm's Qwen3.5 linear attention does (qwen3_5.py)."""

    def __init__(self):
        super().__init__()
        self.in_proj_a = nn.Linear(WIDTH, 2, bias=False)
        self.in_proj_b = nn.Linear(WIDTH, 2, bias=False)
        self.A_log, self.dt_bias = mx.zeros((2,)), mx.zeros((2,))

    def __call__(self, h):
        batch, tokens, _ = h.shape
        q = k = h.reshape(batch, tokens, 2, WIDTH // 2)
        q, k = reference.normalize_qk(q, k, inv_scale=(WIDTH // 2) ** -0.5, eps=1e-6)
        out, _ = qwen3_5.gated_delta_update(
            q,
            k,
            h.reshape(batch, tokens, 2, WIDTH // 2),
            self.in_proj_a(h),
            self.in_proj_b(h),
            self.A_log,
            self.dt_bias,
            None,
            None,
            use_kernel=not self.training,
        )
        return out.reshape(batch, tokens, WIDTH)


class Layer(nn.Module):
    def __init__(self, linear):
        super().__init__()
        if linear:
            self.linear_attn = DeltaNet()
        else:
            self.self_attn = Attention()

    def __call__(self, h):
        if "linear_attn" in self:
            return h + self.linear_attn(h)
        return h + self.self_attn.o_proj(self.self_attn.q_proj(h))


class Text(nn.Module):
    def __init__(self):
        super().__init__()
        self.embed_tokens = nn.Embedding(32, WIDTH)
        self.layers = [Layer(False), Layer(True)]

    def __call__(self, ids, cache=None):
        assert cache is None
        h = self.embed_tokens(ids)
        for layer in self.layers:
            h = layer(h)
        return h


class Holder(nn.Module):
    def __init__(self):
        super().__init__()
        self.model = Text()


class Toy(nn.Module):
    def __init__(self):
        super().__init__()
        self.language_model = Holder()


TARGETS = {
    "layers.0.self_attn.q_proj": (WIDTH, WIDTH),
    "layers.1.linear_attn.in_proj_a": (WIDTH, 2),
}


def toy_package(tmp_path, shapes=TARGETS, seed=0, extra=None):
    """A package as engine.wire_lora and MlxLoraBackbone read it, around a random adapter."""
    from safetensors.numpy import save_file

    rng = np.random.default_rng(seed)
    tensors = {}
    for name, (ins, outs) in shapes.items():
        stem = f"base_model.model.{name}"
        tensors[stem + ".lora_A.weight"] = rng.standard_normal((4, ins)).astype(np.float32)
        tensors[stem + ".lora_B.weight"] = rng.standard_normal((outs, 4)).astype(np.float32)
    tmp_path.mkdir(exist_ok=True)
    save_file(tensors | (extra or {}), tmp_path / "adapter_model.safetensors")
    (tmp_path / "config.json").write_text('{"model_type": "qwen3_5"}')
    return (
        SimpleNamespace(
            root=tmp_path,
            model_dir=tmp_path,
            hidden_size=WIDTH,
            meta={"lora": LORA, "max_row": 512},
            adapter=adapter_layout(tmp_path / "adapter_model.safetensors", LORA),
            runtime=installed_runtime(),
        ),
        tensors,
    )


def test_the_adapter_is_wired_strictly_and_computes_the_low_rank_path(tmp_path):
    mx.random.seed(0)
    lm = Toy()
    lm.freeze()
    text = lm.language_model.model
    plain = text.layers[0].self_attn.q_proj
    package, tensors = toy_package(tmp_path)
    assert engine.wire_lora(lm, package, mx) == 2
    wrapped = text.layers[0].self_attn.q_proj
    assert type(wrapped) is LoRALinear and wrapped.linear is plain
    assert type(text.layers[1].linear_attn.in_proj_a) is LoRALinear
    assert type(text.layers[0].self_attn.o_proj) is nn.Linear  # no adapter tensors: untouched
    assert wrapped.lora_a.dtype == wrapped.lora_b.dtype == mx.float32
    a = tensors["base_model.model.layers.0.self_attn.q_proj.lora_A.weight"]
    b = tensors["base_model.model.layers.0.self_attn.q_proj.lora_B.weight"]
    x = np.random.default_rng(1).standard_normal((3, WIDTH)).astype(np.float32)
    expected = x @ np.array(plain.weight).T + (8 / 4) * (x @ a.T @ b.T)  # scale: alpha / rank
    np.testing.assert_allclose(np.array(wrapped(mx.array(x))), expected, rtol=1e-5, atol=1e-5)


@pytest.mark.parametrize("fault", ["missing_module", "unadapted_module", "shape", "not_dense"])
def test_an_adapter_that_does_not_fit_the_base_exactly_is_refused(tmp_path, fault):
    lm = Toy()
    shapes = dict(TARGETS)
    if fault == "missing_module":  # an adapter tensor with no projection in the base
        shapes["layers.5.self_attn.q_proj"] = (WIDTH, WIDTH)
    elif fault == "unadapted_module":  # a target projection the adapter leaves out
        del shapes["layers.1.linear_attn.in_proj_a"]
    elif fault == "shape":
        shapes["layers.1.linear_attn.in_proj_a"] = (WIDTH, 3)
    elif fault == "not_dense":
        attention = lm.language_model.model.layers[0].self_attn
        attention.q_proj = LoRALinear.from_base(attention.q_proj, r=4)  # adapted already
    package, _ = toy_package(tmp_path, shapes)
    before = dict(lm.named_modules())
    with pytest.raises(ValueError):
        engine.wire_lora(lm, package, mx)
    assert dict(lm.named_modules()) == before  # nothing was half adapted


# --- the backbone: load, adapt, training mode, the one substitution ---


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    """A toy base in mlx-lm's loader's place, and the text body's update as mlx-lm ships it."""
    monkeypatch.setattr(qwen3_5, "gated_delta_update", reference.gated_delta_update)
    steps = []
    mx.random.seed(1)
    lm = Toy()
    lm.eval()  # as mlx-lm's load_model leaves it

    def load_model(path, **kwargs):
        steps.append(("load", path, kwargs))
        return lm, {}

    def set_cache_limit(size):
        steps.append(("cache_limit", size))

    monkeypatch.setattr(mlx_lm.utils, "load_model", load_model)
    monkeypatch.setattr(mx, "set_cache_limit", set_cache_limit)
    package, _ = toy_package(tmp_path / "package")
    return SimpleNamespace(lm=lm, steps=steps, package=package)


def test_unmerged_backbone_runs_the_pinned_program(loaded):
    ids = [3, 1, 4, 1, 5, 9, 2, 6] * 9  # 72 tokens: more than one chunk
    text = loaded.lm.language_model.model
    backbone = engine.MlxLoraBackbone(loaded.package)
    assert backbone.name == "mlx-lora"
    # The reusable-buffer bound precedes the load; the load reads the verified directory only.
    assert loaded.steps == [
        ("cache_limit", engine.MLX_CACHE_BYTES),
        ("load", loaded.package.model_dir, {}),
    ]
    assert backbone.text is text and text.training
    assert qwen3_5.gated_delta_update is delta.gated_delta_update
    assert not loaded.lm.language_model.model.layers[
        0
    ].self_attn.q_proj.linear.trainable_parameters()
    got = backbone.hidden({"ids": ids})
    assert got.shape == (72, WIDTH) and got.dtype == np.float32
    # The same adapted weights with mlx-lm's own token-by-token update: equal within rounding,
    # so the row was answered through the adapter and the chunked update, causally, whole.
    qwen3_5.gated_delta_update = reference.gated_delta_update
    theirs = np.array(text(mx.array([ids], dtype=mx.int32), cache=None))[0]
    np.testing.assert_allclose(got, theirs, rtol=2e-4, atol=2e-5)
    assert np.abs(got - theirs).max() > 0  # and not through mlx-lm's update
    # A second unmerged package in the process shares the one substitution.
    qwen3_5.gated_delta_update = delta.gated_delta_update
    assert delta_calls(lambda: backbone.hidden({"ids": ids})) == 1


def delta_calls(run):
    calls, real = [], delta.chunked_gated_delta

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    delta._checkpointed.clear()
    delta.chunked_gated_delta = counted
    try:
        run()
    finally:
        delta.chunked_gated_delta = real
        delta._checkpointed.clear()
    return len(calls)


def test_a_displaced_runtime_stops_the_answers(loaded):
    backbone = engine.MlxLoraBackbone(loaded.package)
    backbone.hidden({"ids": [1, 2, 3]})
    backbone.text.eval()  # the kernel path: not the package's program
    with pytest.raises(RuntimeError, match="no longer in place"):
        backbone.hidden({"ids": [1, 2, 3]})
    backbone.text.train()
    backbone.hidden({"ids": [1, 2, 3]})
    qwen3_5.gated_delta_update = reference.gated_delta_update
    with pytest.raises(RuntimeError, match="no longer in place"):
        backbone.hidden({"ids": [1, 2, 3]})


def other_update(*_, **__):
    pytest.fail("another update ran")


@pytest.mark.parametrize(
    "fault", ["other_substitution", "other_source", "other_delta", "model_file", "quantization"]
)
def test_a_runtime_that_cannot_be_the_pinned_one_is_refused_before_the_load(loaded, fault):
    package = loaded.package
    elsewhere = Path(package.root) / "elsewhere.py"
    if fault == "other_substitution":
        qwen3_5.gated_delta_update = other_update
    elif fault == "other_source":  # the hashes were taken of other files than the ones imported
        package.runtime["sources"]["qwen3_5.py"] = elsewhere
    elif fault == "other_delta":
        package.runtime["delta"] = elsewhere
    elif fault == "model_file":
        (package.model_dir / "config.json").write_text('{"model_file": "model.py"}')
    elif fault == "quantization":
        (package.model_dir / "config.json").write_text('{"text_config": {"quantization": {}}}')
    before = qwen3_5.gated_delta_update
    with pytest.raises(ValueError):
        engine.MlxLoraBackbone(package)
    assert not loaded.steps and qwen3_5.gated_delta_update is before
    assert not loaded.lm.language_model.model.training


@pytest.mark.parametrize("fault", ["adapter", "float16"])
def test_a_failed_load_leaves_mlx_lm_as_it_was(loaded, fault):
    if fault == "adapter":
        del loaded.package.adapter["language_model.model.layers.0.self_attn.q_proj"]
    else:
        attention = loaded.lm.language_model.model.layers[0].self_attn
        attention.o_proj.weight = attention.o_proj.weight.astype(mx.float16)
    with pytest.raises(ValueError):
        engine.MlxLoraBackbone(loaded.package)
    assert qwen3_5.gated_delta_update is reference.gated_delta_update  # never half applied
    assert not loaded.lm.language_model.model.training
