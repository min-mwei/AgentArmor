"""CPU contracts for a second registered base: an unmerged package of the sparse-MoE
Qwen3.6-35B-A3B base (schema agentarmor-qwen-lora/v2), what it must state, what it is served
as, and how its frozen routed-expert banks are quantized and kept out of the adapter.

Synthetic throughout: a made-up base directory, adapter and runtime, and a toy text body. No
model weights, no GPU, no network. The toy-model tests need the serving environment's MLX and
mlx-lm and are skipped without them.
"""

import json
from types import SimpleNamespace

import numpy as np
import pytest
from agentarmor_vllm import engine, server
from agentarmor_vllm.package import (
    BASE,
    BASE_REVISION,
    BASES,
    LORA_SCHEMA,
    LORA_SCHEMA_V2,
    MOE_BASE,
    MOE_BASE_REVISION,
    MOE_SOURCES,
    LoraPackage,
    adapter_layout,
    sha256,
)
from agentarmor_vllm.requests import validate_request
from safetensors.numpy import save_file
from test_lora_package import Made, never, stub_tokenizer
from test_mlx_backbone import head

QUANTIZATION = {"bits": 4, "group_size": 64, "mode": "affine"}
MOE_TEXT = {
    "model_type": "qwen3_5_moe_text",
    "hidden_size": 2048,
    "num_hidden_layers": 40,
    "num_experts": 256,
    "num_experts_per_tok": 8,
}


def second(made, base=MOE_BASE):
    """Turn the synthetic 4B package into a v2 package of ``base``, every hash rebound."""
    runtime = made.installed["delta"].parent
    made.meta |= {"schema": LORA_SCHEMA_V2, "base": base, "base_revision": BASES[base]["revision"]}
    made.meta["routed_experts"] = None
    if BASES[base]["mlp"] == "sparse-moe":
        for name in MOE_SOURCES:
            if not (runtime / name).exists():
                (runtime / name).write_text(f"# {name}\n")
        made.installed["sources"] = {name: runtime / name for name in MOE_SOURCES}
        made.meta["runtime"]["mlx_lm_sources"] = {
            name: sha256(runtime / name) for name in MOE_SOURCES
        }
        made.meta["routed_experts"] = {"quantization": dict(QUANTIZATION)}
        save_file(head(2048), made.root / "pointer.safetensors")
        made.meta["files"]["pointer.safetensors"] = sha256(made.root / "pointer.safetensors")
        made.rebase({"model_type": "qwen3_5_moe", "text_config": dict(MOE_TEXT)})
    made.write()
    return made


@pytest.fixture
def moe(tmp_path_factory, monkeypatch):
    return second(Made(tmp_path_factory.mktemp("moe"), monkeypatch))


def test_the_registry_names_two_bases_each_served_under_its_own_name():
    assert set(BASES) == {BASE, MOE_BASE}
    assert BASES[BASE]["revision"] == BASE_REVISION and BASES[BASE]["model"] == "kai-4b"
    assert BASES[MOE_BASE]["revision"] == MOE_BASE_REVISION
    assert BASES[MOE_BASE]["model"] == "kai-35b-a3b" and BASES[MOE_BASE]["hidden_size"] == 2048
    assert len({base["model"] for base in BASES.values()}) == len(BASES)


def test_a_sparse_moe_package_is_accepted_and_says_what_it_is(moe):
    package = LoraPackage(moe.root, base_dir=moe.base)
    assert package.model == "kai-35b-a3b" and package.hidden_size == 2048
    assert package.base is BASES[MOE_BASE] and package.routed_experts == QUANTIZATION
    assert package.identity == sha256(moe.root / "kai.json")


def test_a_dense_package_may_use_the_second_schema_and_states_no_expert_quantization(
    tmp_path, monkeypatch
):
    made = second(Made(tmp_path, monkeypatch), base=BASE)
    package = LoraPackage(made.root, base_dir=made.base)
    assert package.model == "kai-4b" and package.hidden_size == 2560
    assert package.routed_experts is None


def unstated(m):
    m.meta["routed_experts"] = None


def absent(m):
    del m.meta["routed_experts"]


def unregistered_bits(m):
    m.meta["routed_experts"]["quantization"]["bits"] = 3


def unregistered_mode(m):
    m.meta["routed_experts"]["quantization"]["mode"] = "mxfp4"


def extra_setting(m):
    m.meta["routed_experts"]["dtype"] = "bfloat16"


def first_schema(m):  # v1 is the 4B base's alone
    m.meta["schema"] = LORA_SCHEMA
    del m.meta["routed_experts"]


def other_revision(m):
    m.meta["base_revision"] = "0" * 40


def unregistered_base(m):
    m.meta["base"] = "Qwen/Qwen3.6-27B"


def dense_sources_only(m):  # the expert block and its banks must be pinned too
    for name in ("qwen3_5_moe.py", "qwen3_next.py", "switch_layers.py"):
        del m.meta["runtime"]["mlx_lm_sources"][name]


def changed_expert_block(m):
    with m.installed["sources"]["qwen3_next.py"].open("a") as stream:
        stream.write("# changed after the package was made\n")


def other_width(m):
    m.rebase({"model_type": "qwen3_5_moe", "text_config": MOE_TEXT | {"hidden_size": 2560}})


def other_experts(m):
    m.rebase({"model_type": "qwen3_5_moe", "text_config": MOE_TEXT | {"num_experts": 128}})


def other_fanout(m):
    m.rebase({"model_type": "qwen3_5_moe", "text_config": MOE_TEXT | {"num_experts_per_tok": 4}})


def stored_quantized(m):
    m.rebase(
        {"model_type": "qwen3_5_moe", "text_config": dict(MOE_TEXT), "quantization": {"bits": 4}}
    )


FAULTS = [
    unstated,
    absent,
    unregistered_bits,
    unregistered_mode,
    extra_setting,
    first_schema,
    other_revision,
    unregistered_base,
    dense_sources_only,
    changed_expert_block,
    other_width,
    other_experts,
    other_fanout,
    stored_quantized,
]


@pytest.mark.parametrize("fault", FAULTS, ids=lambda fault: fault.__name__)
def test_a_sparse_moe_package_that_misstates_anything_is_refused(moe, monkeypatch, fault):
    fault(moe)
    moe.write()
    with pytest.raises(ValueError):
        LoraPackage(moe.root, base_dir=moe.base)
    opened = stub_tokenizer(monkeypatch, moe.base)
    monkeypatch.setattr(engine, "MlxLoraBackbone", never)
    with pytest.raises(ValueError):
        engine.load_service(moe.root, backend="mlx", base_dir=moe.base)
    assert not opened


def test_a_dense_package_cannot_state_expert_quantization(tmp_path, monkeypatch):
    made = second(Made(tmp_path, monkeypatch), base=BASE)
    made.meta["routed_experts"] = {"quantization": dict(QUANTIZATION)}
    made.write()
    with pytest.raises(ValueError):
        LoraPackage(made.root, base_dir=made.base)


def test_the_service_answers_as_the_package_s_model_and_as_no_other(moe, monkeypatch):
    class Backbone:
        name = "mlx-lora"

        def __init__(self, package):
            self.package = package

    monkeypatch.setattr(engine, "MlxLoraBackbone", Backbone)
    stub_tokenizer(monkeypatch, moe.base)
    service = engine.load_service(moe.root, backend="mlx", base_dir=moe.base)
    told = service.metadata()
    assert told["model"] == "kai-35b-a3b" and told["weights"] == "lora-unmerged"
    assert told["policy_ready"] is False and told["package_sha256"] == service.package.identity
    assert service.pointer.w["q.weight"].shape == (2, 2048)


@pytest.mark.parametrize(
    ("resident", "named", "admitted"),
    [
        ("kai-35b-a3b", None, True),
        ("kai-35b-a3b", "kai", True),
        ("kai-35b-a3b", "kai-35b-a3b", True),
        ("kai-35b-a3b", "Kai-35B-A3B", True),
        ("kai-35b-a3b", "kai-4b", False),
        ("kai-4b", "kai-4b", True),
        ("kai-4b", "kai-35b-a3b", False),
    ],
)
def test_a_request_may_name_only_the_resident_model(resident, named, admitted):
    request = {"state": "s", "questions": {"q": {"type": "noul", "instructions": "i"}}}
    request |= {"model": named} if named else {}
    if admitted:
        assert validate_request(request, resident).questions
    else:
        with pytest.raises(ValueError):
            validate_request(request, resident)


def test_the_server_refuses_a_package_of_another_model_than_the_one_named(moe, monkeypatch, capsys):
    stub_tokenizer(monkeypatch, moe.base)
    argv = ["--package", str(moe.root), "--backend", "mlx", "--base-dir", str(moe.base)]
    server.main([*argv, "--verify-only", "--model", "kai-35b-a3b"])
    told = json.loads(capsys.readouterr().out)
    assert told["model"] == "kai-35b-a3b" and told["schema"] == LORA_SCHEMA_V2
    with pytest.raises(SystemExit):
        server.main([*argv, "--verify-only", "--model", "kai-4b"])


# --- the toy text body: quantized banks, and an adapter that leaves them alone ---

WIDTH, EXPERTS, LAYERS = 64, 4, 2
LORA = {"rank": 4, "alpha": 8, "targets": ["q_proj", "gate_proj", "up_proj", "down_proj"]}
SMALL = {"bits": 4, "group_size": 32, "mode": "affine"}


def toy(mx, nn):
    from mlx_lm.models.switch_layers import SwitchGLU

    class Shared(nn.Module):
        def __init__(self):
            super().__init__()
            self.gate_proj = nn.Linear(WIDTH, WIDTH, bias=False)
            self.up_proj = nn.Linear(WIDTH, WIDTH, bias=False)
            self.down_proj = nn.Linear(WIDTH, WIDTH, bias=False)

    class Mlp(nn.Module):
        def __init__(self):
            super().__init__()
            self.switch_mlp = SwitchGLU(WIDTH, WIDTH, EXPERTS)
            self.shared_expert = Shared()

    class Attention(nn.Module):
        def __init__(self):
            super().__init__()
            self.q_proj = nn.Linear(WIDTH, WIDTH, bias=False)

    class Layer(nn.Module):
        def __init__(self):
            super().__init__()
            self.self_attn, self.mlp = Attention(), Mlp()

    class Text(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = [Layer() for _ in range(LAYERS)]

    class Language(nn.Module):
        def __init__(self):
            super().__init__()
            self.model = Text()
            self.lm_head = nn.Linear(WIDTH, 16, bias=False)

    class Toy(nn.Module):
        def __init__(self):
            super().__init__()
            self.language_model = Language()

    mx.random.seed(3)
    return Toy()


def toy_package(tmp_path, layers=LAYERS):
    names = [f"layers.{i}.self_attn.q_proj" for i in range(LAYERS)]
    names += [
        f"layers.{i}.mlp.shared_expert.{leaf}"
        for i in range(LAYERS)
        for leaf in ("gate_proj", "up_proj", "down_proj")
    ]
    tensors = {}
    rng = np.random.default_rng(0)
    for name in names:
        stem = f"base_model.model.{name}"
        tensors[stem + ".lora_A.weight"] = rng.standard_normal((4, WIDTH)).astype(np.float32)
        tensors[stem + ".lora_B.weight"] = rng.standard_normal((WIDTH, 4)).astype(np.float32)
    tmp_path.mkdir(parents=True, exist_ok=True)
    save_file(tensors, tmp_path / "adapter_model.safetensors")
    return SimpleNamespace(
        root=tmp_path,
        meta={"lora": LORA},
        adapter=adapter_layout(tmp_path / "adapter_model.safetensors", LORA),
        base={"mlp": "sparse-moe", "layers": layers, "sources": MOE_SOURCES},
        routed_experts=dict(SMALL),
    )


@pytest.fixture
def mlx():
    mx = pytest.importorskip("mlx.core")
    pytest.importorskip("mlx_lm")
    import mlx.nn as nn

    before = mx.default_device()
    mx.set_default_device(mx.cpu)
    yield SimpleNamespace(mx=mx, nn=nn)
    mx.set_default_device(before)


def test_the_banks_are_quantized_as_stated_and_the_adapter_leaves_them_alone(mlx, tmp_path):
    from mlx_lm.models.switch_layers import QuantizedSwitchLinear
    from mlx_lm.tuner.lora import LoRALinear

    lm = toy(mlx.mx, mlx.nn)
    package = toy_package(tmp_path)
    x = mlx.mx.array(np.random.default_rng(1).standard_normal((1, 3, WIDTH)).astype(np.float32))
    picked = mlx.mx.array([[[0, 2], [1, 3], [2, 0]]])
    stored = lm.language_model.model.layers[0].mlp.switch_mlp(x, picked)
    assert engine.quantize_routed_experts(lm, SMALL, mlx.mx) == 3 * LAYERS
    banks = engine.routed_banks(lm, package)
    assert len(banks) == 3 * LAYERS and all(
        type(bank) is QuantizedSwitchLinear and bank.bits == 4 and bank.group_size == 32
        for bank in banks.values()
    )
    # Four-bit banks answer near the stored ones, and not exactly as them.
    quantized = lm.language_model.model.layers[0].mlp.switch_mlp(x, picked)
    assert quantized.shape == stored.shape
    assert not np.array_equal(np.array(quantized), np.array(stored))
    lm.freeze()
    assert engine.wire_lora(lm, package, mlx.mx) == 4 * LAYERS
    mlp = lm.language_model.model.layers[1].mlp
    assert type(mlp.shared_expert.gate_proj) is LoRALinear  # the shared expert is adapted
    assert type(mlp.switch_mlp.gate_proj) is QuantizedSwitchLinear  # a routed bank is not
    assert type(lm.language_model.model.layers[0].self_attn.q_proj) is LoRALinear


@pytest.mark.parametrize("fault", ["stored", "other_bits", "layer_count"])
def test_banks_that_are_not_what_the_package_states_are_refused(mlx, tmp_path, fault):
    lm = toy(mlx.mx, mlx.nn)
    package = toy_package(tmp_path, layers=LAYERS + (fault == "layer_count"))
    if fault != "stored":
        spec = SMALL | ({"bits": 8} if fault == "other_bits" else {})
        engine.quantize_routed_experts(lm, spec, mlx.mx)
    lm.freeze()
    before = dict(lm.named_modules())
    with pytest.raises(ValueError):
        engine.wire_lora(lm, package, mlx.mx)
    assert dict(lm.named_modules()) == before  # nothing was half adapted


def test_quantizing_twice_or_a_dense_decoder_is_refused(mlx):
    lm = toy(mlx.mx, mlx.nn)
    engine.quantize_routed_experts(lm, SMALL, mlx.mx)
    with pytest.raises(ValueError):
        engine.quantize_routed_experts(lm, SMALL, mlx.mx)
    dense = toy(mlx.mx, mlx.nn)
    del dense.language_model.model.layers[0].mlp["switch_mlp"]
    with pytest.raises(ValueError):
        engine.quantize_routed_experts(dense, SMALL, mlx.mx)


# --- the monitor: which decision model an operator says Kai is ---


def test_the_monitor_reads_an_answer_only_from_the_model_the_operator_named():
    from agentarmor_monitor import kai

    named = "a" * 64
    expected = kai.profile(named, "kai-35b-a3b")
    assert expected == {
        "adapter": "kai-35b-a3b",
        "model": "kai-35b-a3b",
        "readout": "pointer",
        "package_sha256": named,
    }
    said = {"model": "kai-35b-a3b", "readout": "pointer", "package_sha256": named}
    kai.admit(said, expected)
    with pytest.raises(kai.KaiMismatch):
        kai.admit(said | {"model": "kai-4b"}, expected)
    with pytest.raises(kai.KaiMismatch):  # the default profile is still Kai-4B's
        kai.admit(said, kai.profile(named))
    with pytest.raises(ValueError):
        kai.profile(named, "kai-70b")


def test_the_monitor_s_model_option_names_the_expected_profile():
    from agentarmor_monitor import cli

    named = "b" * 64
    told = SimpleNamespace(kai_package_sha256=named, kai_model="kai-35b-a3b")
    assert cli._expected(told)["model"] == "kai-35b-a3b"
    assert cli._expected(SimpleNamespace(kai_package_sha256=named))["model"] == "kai-4b"
    assert cli._expected(SimpleNamespace(kai_package_sha256=None, kai_model="kai-35b-a3b")) is None


def test_the_backbone_loads_a_sparse_moe_base_as_the_package_states(mlx, tmp_path, monkeypatch):
    import mlx_lm.utils
    from agentarmor_vllm import delta
    from agentarmor_vllm.package import installed_runtime
    from mlx_lm.models import gated_delta as reference
    from mlx_lm.models import qwen3_5
    from mlx_lm.models.switch_layers import QuantizedSwitchLinear
    from mlx_lm.tuner.lora import LoRALinear

    monkeypatch.setattr(qwen3_5, "gated_delta_update", reference.gated_delta_update)
    lm, calls = toy(mlx.mx, mlx.nn), []

    def load_model(path, **kwargs):
        calls.append((path, kwargs))
        return lm, {}

    monkeypatch.setattr(mlx_lm.utils, "load_model", load_model)
    monkeypatch.setattr(mlx.mx, "set_cache_limit", lambda size: None)
    package = toy_package(tmp_path / "package")
    (tmp_path / "config.json").write_text('{"model_type": "qwen3_5_moe"}')
    package.model_dir, package.hidden_size = tmp_path, WIDTH
    package.meta |= {"max_row": 512}
    package.runtime = installed_runtime()
    backbone = engine.MlxLoraBackbone(package)
    assert calls == [(tmp_path, {"lazy": True})]  # read lazily: the banks are quantized first
    assert "lm_head" not in lm.language_model  # the vocabulary head is never evaluated
    mlp = lm.language_model.model.layers[0].mlp
    assert type(mlp.switch_mlp.up_proj) is QuantizedSwitchLinear
    assert mlp.switch_mlp.up_proj.weight.dtype == mlx.mx.uint32
    assert type(mlp.shared_expert.up_proj) is LoRALinear
    # The package's program is in place: training mode and this package's DeltaNet update.
    assert backbone.in_place() and lm.language_model.model.training
    assert qwen3_5.gated_delta_update is delta.gated_delta_update


def test_every_source_a_sparse_moe_package_pins_must_be_the_module_in_use(mlx, tmp_path):
    from agentarmor_vllm.package import installed_runtime

    package = toy_package(tmp_path)
    package.runtime = installed_runtime()
    engine.delta_update(package)  # all five sources are the installed modules' own files
    for name in MOE_SOURCES:
        elsewhere = dict(package.runtime, sources=dict(package.runtime["sources"]))
        elsewhere["sources"][name] = tmp_path / name  # hashed from another file than is imported
        with pytest.raises(ValueError):
            engine.delta_update(SimpleNamespace(base=package.base, runtime=elsewhere))


def test_a_model_named_at_load_is_checked_before_a_backbone_exists(moe, monkeypatch):
    stub_tokenizer(monkeypatch, moe.base)
    monkeypatch.setattr(engine, "MlxLoraBackbone", never)
    with pytest.raises(ValueError, match="not of the model named"):
        engine.load_service(moe.root, backend="mlx", base_dir=moe.base, model="kai-4b")
