"""Raw causal hidden states from vLLM, followed by Kai's FP32 pointer head."""

import math

import numpy as np

from .api import to_answers, to_record
from .encode import SPECIAL, encode, pause_token, rows_of, tokenizer_sha256
from .requests import MAX_TOTAL_TOKENS, validate_request


class Pointer:
    def __init__(self, weights, hidden_size):
        if set(weights) != {"q.weight", "q.bias", "k.weight", "k.bias"}:
            raise ValueError("pointer tensor names differ")
        self.w = {key: np.asarray(value, dtype=np.float32) for key, value in weights.items()}
        dp = self.w["q.bias"].size
        if not 1 <= dp <= 4096:
            raise ValueError("invalid pointer dimension")
        for key, value in self.w.items():
            expected = (dp, hidden_size) if key.endswith("weight") else (dp,)
            if value.shape != expected or not np.isfinite(value).all():
                raise ValueError("invalid pointer tensor")
        self.scale = np.float32(1 / math.sqrt(dp))

    def logits(self, hidden, row):
        h = np.asarray(hidden, dtype=np.float32)
        if h.shape != (len(row["ids"]), self.w["q.weight"].shape[1]):
            raise ValueError("backbone returned incomplete hidden states")
        picked = h[[row["decide"], *row["opts"]]]
        if not np.isfinite(h).all():
            raise ValueError("nonfinite hidden states")
        q = self.w["q.weight"] @ picked[0] + self.w["q.bias"]
        k = picked[1:] @ self.w["k.weight"].T + self.w["k.bias"]
        logits = (k @ q) * self.scale
        if not np.isfinite(logits).all():
            raise ValueError("nonfinite pointer output")
        return logits


class VllmBackbone:
    def __init__(self, package):
        from importlib.metadata import version

        from vllm.config import PoolerConfig

        from vllm import LLM

        if version("vllm") != "0.33.0":
            raise ValueError("baseline is pinned to vLLM 0.33.0")
        self.llm = LLM(
            model=str(package.root / "model"),
            runner="pooling",
            convert="embed",
            dtype="bfloat16",
            trust_remote_code=False,
            language_model_only=True,
            hf_overrides={"is_causal": True},
            enforce_eager=True,
            max_model_len=package.meta["max_row"],
            max_num_seqs=1,
            max_num_batched_tokens=package.meta["max_row"],
            enable_prefix_caching=False,
            enable_chunked_prefill=False,
            pooler_config=PoolerConfig(
                task="token_embed", tok_pooling_type="ALL", use_activation=False
            ),
        )

    def hidden(self, row):
        # Independent complete causal rows also isolate DeltaNet's recurrent state.
        outputs = self.llm.encode(
            [{"prompt_token_ids": row["ids"]}], pooling_task="token_embed", use_tqdm=False
        )
        if len(outputs) != 1:
            raise ValueError("missing backbone result")
        output = outputs[0]
        if output.prompt_token_ids != row["ids"]:
            raise ValueError("backbone changed or truncated the row")
        return output.outputs.data.detach().float().cpu().numpy()


class Service:
    def __init__(self, package, tokenizer, pointer, backbone, source=None):
        self.package, self.tok, self.pointer, self.backbone = package, tokenizer, pointer, backbone
        self.source = source or {}
        m = package.meta
        if tokenizer_sha256(tokenizer) != m["tokenizer_sha256"]:
            raise ValueError("tokenizer identity mismatch")
        ids = [tokenizer.convert_tokens_to_ids(t) for t in SPECIAL]
        if len(set(ids)) != 5 or any(
            type(i) is not int or i < 0 or i == tokenizer.unk_token_id for i in ids
        ):
            raise ValueError("tokenizer lacks Kai delimiters")
        if m["pauses"] and pause_token(tokenizer) != m["pause_id"]:
            raise ValueError("pause token mismatch")

    def metadata(self):
        return {
            **self.source,
            "backend": "vllm",
            "model": "kai-4b",
            "readout": "pointer",
            "policy_ready": False,
            "evidence_available": False,
            "package_sha256": self.package.identity,
            "diagnostic": True,
        }

    def evaluate(self, value):
        req = validate_request(value)
        rec, meta = to_record(req)
        m = self.package.meta
        enc = encode(
            self.tok,
            rec,
            max_state=m["max_state"],
            max_branch=m["max_row"] - m["pauses"],
            strict=True,
            pauses=m["pauses"],
            pause_id=m["pause_id"],
        )
        if len(enc["ids"]) > m["max_packed"]:
            raise ValueError("packed input exceeds checkpoint context")
        state, _, branches = rows_of(enc)
        # Admission precedes every forward; bound duplicated state work as well as packed input.
        if sum(len(state) + len(r["ids"]) for r in branches) > MAX_TOTAL_TOKENS:
            raise ValueError("request exceeds total row-token budget")
        probs = []
        for r in branches:
            row = {
                "ids": state + r["ids"],
                "decide": len(state) + r["decide"],
                "opts": [len(state) + p for p in r["opts"]],
            }
            logits = self.pointer.logits(self.backbone.hidden(row), row).astype(np.float64)
            logits = (logits - logits.max()) / m["temperature"]
            p = np.exp(logits)
            p /= p.sum()
            if not np.isfinite(p).all():
                raise ValueError("nonfinite probabilities")
            probs.append(p.tolist())
        return {
            **self.metadata(),
            "answers": to_answers(probs, meta),
            "coverage": "complete",
        }


def load_service(directory, *, hf_blobs=None, source=None):
    from safetensors.numpy import load_file
    from transformers import AutoTokenizer

    from .package import Package

    package = Package(directory, hf_blobs=hf_blobs)
    tokenizer = AutoTokenizer.from_pretrained(
        package.root / "model", local_files_only=True, trust_remote_code=False
    )
    pointer = Pointer(load_file(package.root / "pointer.safetensors"), package.hidden_size)
    # Validate tokenizer/head before allocating a GPU engine.
    service = Service(package, tokenizer, pointer, None, source=source)
    service.backbone = VllmBackbone(package)
    return service
