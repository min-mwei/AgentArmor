"""Lazy vLLM loading keeps hook clients and CPU development GPU-independent."""

import importlib.util
import json
import os
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Protocol

from armor.config import Settings
from armor.encoding import encode_rows
from armor.readout import EvidenceReadout, PointerReadout, answer
from armor.schemas import DecisionRequest
from armor.v0 import contract


class Backend(Protocol):
    evidence_available: bool

    def decide(self, request: DecisionRequest) -> dict: ...


def resolve_snapshot(settings: Settings) -> Path:
    local = Path(settings.model).expanduser()
    if local.is_dir():
        return local.resolve()
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(repo_id=settings.model, revision=settings.revision))


def inspect_model(settings: Settings) -> dict:
    """Inspect file names without downloading weights or importing torch/vLLM."""
    local = Path(settings.model).expanduser()
    if local.is_dir():
        files = [str(path.relative_to(local)) for path in local.rglob("*") if path.is_file()]
    else:
        from huggingface_hub import HfApi

        files = HfApi().list_repo_files(settings.model, revision=settings.revision)
    adapter = "adapter_config.json" in files and "adapter_model.safetensors" in files
    full = "config.json" in files and any(
        name.startswith("model") and name.endswith(".safetensors") for name in files
    )
    return {
        "model": settings.model,
        "revision": settings.revision,
        "files": sorted(files),
        "checkpoint_files_present": "head.pt" in files and (adapter or full),
        "evidence_head_present": "evidence_head.safetensors" in files,
        "note": "File presence only; runtime compatibility and GPU parity require validation.",
    }


class VLLMBackend:
    def __init__(self, settings: Settings):
        # Inspect the release before attempting a large download.
        if not inspect_model(settings)["checkpoint_files_present"]:
            raise RuntimeError(
                f"{settings.model} is not a published Kai checkpoint yet. Expected head.pt, "
                "a tokenizer, and either adapter weights/config or full model weights/config. "
                "Use 'agentarmor inspect' to inspect the release."
            )
        try:
            import torch

            metal = bool(importlib.util.find_spec("vllm_metal"))
            if metal:
                os.environ["VLLM_ENABLE_V1_MULTIPROCESSING"] = "0"
            from vllm import LLM
            from vllm.config import PoolerConfig
        except ImportError as exc:
            raise RuntimeError(
                "Run python install.py --backend metal (macOS) or cuda (Linux)"
            ) from exc

        if metal:
            from armor.metal import enable_token_embeddings

            enable_token_embeddings()

        snapshot = resolve_snapshot(settings)
        contract(snapshot)
        meta = torch.load(snapshot / "head.pt", map_location="cpu", weights_only=True)
        if (
            meta.get("readout", "pointer") != "pointer"
            or meta.get("attention", "causal") != "causal"
            or meta.get("option_isolation", False)
            or meta.get("layout") is not None
        ):
            raise ValueError(
                "Only causal pointer checkpoints without option isolation or pauses are supported"
            )
        weights = {key: tensor.detach().float().numpy() for key, tensor in meta["head"].items()}
        self.pointer = PointerReadout(weights, float(meta.get("temperature", 1.0)))
        evidence_path = snapshot / "evidence_head.safetensors"
        self.evidence = None
        if evidence_path.exists():
            from safetensors.numpy import load_file

            self.evidence = EvidenceReadout(load_file(evidence_path), self.pointer.hidden_size)
        self.evidence_available = self.evidence is not None
        self.lora_request = None
        adapter_path = snapshot / "adapter_config.json"
        engine_model, revision = str(snapshot), None
        extra = {}
        if adapter_path.exists():
            from vllm.lora.request import LoRARequest

            adapter = json.loads(adapter_path.read_text())
            if meta.get("special_embeddings") or adapter.get("trainable_token_indices"):
                raise ValueError(
                    "Merge trained token embeddings into a full checkpoint before serving"
                )
            if adapter.get("modules_to_save") or adapter.get("use_dora"):
                raise ValueError(
                    "This backend requires a plain LoRA adapter or a merged checkpoint"
                )
            engine_model, revision = meta["base"], meta.get("base_revision")
            extra = {"enable_lora": True, "max_lora_rank": max(16, int(adapter["r"]))}
            self.lora_request = LoRARequest("kai", 1, str(snapshot))
        else:
            config = json.loads((snapshot / "config.json").read_text())
            # Kai's full checkpoints save a text AutoModel without the vocabulary head.
            if config.get("architectures") == ["Qwen3_5TextModel"]:
                extra["hf_overrides"] = {"architectures": ["Qwen3_5ForCausalLM"]}

        cache_bytes = settings.kv_cache_memory_mb
        if cache_bytes is not None:
            extra["kv_cache_memory_bytes"] = cache_bytes * 1024**2
        elif metal:
            # One question at a time needs a small cache, not 85% of the Mac's
            # shared memory. Scale the budget with the context window.
            extra["kv_cache_memory_bytes"] = max(256 * 1024**2, settings.max_model_len * 65536)

        self.batch_size = 1 if metal else settings.max_num_seqs
        self.llm = LLM(
            model=engine_model,
            revision=revision,
            tokenizer=str(snapshot),
            runner="pooling",
            convert="embed",
            language_model_only=True,
            pooler_config=PoolerConfig(task="token_embed", use_activation=False),
            dtype=settings.dtype,
            tensor_parallel_size=settings.tensor_parallel_size,
            gpu_memory_utilization=settings.gpu_memory_utilization,
            max_model_len=settings.max_model_len,
            # A Qwen3.5 recurrent state must remain isolated per question on Metal.
            max_num_seqs=self.batch_size,
            enable_chunked_prefill=False,
            trust_remote_code=False,
            enable_prefix_caching=False,
            enforce_eager=True,
            **extra,
        )
        self.tokenizer = self.llm.get_tokenizer()
        self.settings = settings
        self.lock = threading.Lock()

    def decide(self, request: DecisionRequest) -> dict:
        rows = encode_rows(self.tokenizer, request, self.settings.max_model_len)
        answers, token_count = {}, 0
        # The synchronous engine is shared by all HTTP request threads.
        with self.lock:
            for start in range(0, len(rows), self.batch_size):
                batch = rows[start : start + self.batch_size]
                outputs = self.llm.encode(
                    [{"prompt_token_ids": row.token_ids} for row in batch],
                    pooling_task="token_embed",
                    lora_request=self.lora_request,
                    use_tqdm=False,
                )
                for row, output in zip(batch, outputs, strict=True):
                    hidden = output.outputs.data.detach().float().cpu().numpy()
                    probabilities = self.pointer.probabilities(hidden, row)
                    result = answer(row, probabilities)
                    if self.evidence:
                        result["evidence_spans"] = self.evidence.spans(hidden, row, request.state)
                    answers[row.question_id] = result
                    token_count += len(row.token_ids)
        return {
            "answers": answers,
            "usage": {"input_tokens": token_count, "output_tokens": 0},
            "evidence_available": self.evidence_available,
        }


class StubBackend:
    """Explicit, deny-only smoke backend. Never an approximation of Kai."""

    evidence_available = False

    def decide(self, request: DecisionRequest) -> dict:
        if any(question.type != "noul" for question in request.questions.values()):
            raise ValueError("The stub supports only Kai v0 yes/no questions")
        answers = {}
        for question_id in request.questions:
            value = 0.0 if question_id == "user_requested_exact_operation" else 1.0
            answers[question_id] = {
                "type": "noul",
                "noul": value,
                "probabilities": {"false": 1 - value, "true": value},
                "evidence_spans": [],
            }
        return {
            "answers": answers,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "evidence_available": False,
        }


class ReferenceBackend:
    """Development bridge to a separately running Kai v0 reference server (for example MLX)."""

    evidence_available = False

    def __init__(self, settings: Settings):
        self.url = settings.reference_url.rstrip("/")
        if urllib.parse.urlsplit(self.url).scheme not in ("http", "https"):
            raise ValueError("ARMOR_REFERENCE_URL must be an HTTP(S) URL")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(self.url + "/v1/models", timeout=5) as response:
            if not json.load(response).get("data"):
                raise ValueError("No model is ready at the Kai reference server")

    def decide(self, request: DecisionRequest) -> dict:
        body = request.model_dump(exclude_none=True)
        # The reference server's default alias; an unmodified kai.core.serve accepts it.
        body["model"] = "kev-latest"
        http_request = urllib.request.Request(
            self.url + "/v1/systemone",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(http_request, timeout=120) as response:
            result = json.load(response)
        for value in result["answers"].values():
            value.setdefault("evidence_spans", [])
        return {
            "answers": result["answers"],
            "usage": result.get("usage", {}),
            "evidence_available": False,
        }
