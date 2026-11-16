"""Raw causal hidden states from vLLM or MLX, followed by Kai's FP32 pointer head."""

import hashlib
import math
from pathlib import Path

import numpy as np

from .api import to_answers, to_record
from .encode import SPECIAL, encode, pause_token, rows_of, state_offsets, tokenizer_sha256
from .pairs import PairReadout
from .requests import MAX_TOTAL_TOKENS, validate_request
from .wire import read_json


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
    name = "vllm"

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


# MLX keeps freed buffers for reuse and by default never returns them. Rows differ in length, so
# each forward's buffers fit no earlier ones: measured on 164 forwards of 609 to 1,983 tokens, the
# cache grew to 85.7 GB while live memory stayed at 8.4 GB (peak 9.3 GB). The bound is on reusable
# buffers only; it changes no value.
MLX_CACHE_BYTES = 2 * 1024**3


class MlxBackbone:
    """The same raw causal hidden states through MLX, the local Apple-silicon backend.

    CPU tests pass ``text`` (a stand-in for the loaded text body) and ``mx`` (for ``mlx.core``).
    """

    name = "mlx"

    def __init__(self, package, *, text=None, mx=None):
        self.width, self.max_row = package.hidden_size, package.meta["max_row"]
        if text is None:
            model = package.root / "model"
            config = read_json((model / "config.json").read_bytes())
            # mlx-lm would import a config's model_file as model code (vLLM runs with
            # trust_remote_code=False) or quantize on load; the export is plain merged BF16.
            keys = {"model_file", "quantization", "quantization_config"}
            if keys & set(config) or keys & set(config.get("text_config", {})):
                raise ValueError("export config names model code or a quantization")
            import mlx.core as mx
            from mlx.utils import tree_flatten
            from mlx_lm.utils import load_model

            mx.set_cache_limit(MLX_CACHE_BYTES)  # before the first allocation
            lm = load_model(model)[0]  # this directory's config and safetensors; no hub access
            lm.eval()
            # Weights as stored, as in Kai's loader (backbone.py:105): the merged export is BF16
            # except the DeltaNet tensors it keeps in FP32 (linear_attn.A_log, linear_attn.norm).
            # Any other number type, float16 above all, is refused.
            if any(
                p.dtype not in (mx.bfloat16, mx.float32) for _, p in tree_flatten(lm.parameters())
            ):
                raise ValueError(
                    "MLX backbone requires bfloat16 weights (float32 where the export keeps it)"
                )
            text = lm.language_model.model
        self.text, self.mx = text, mx

    def hidden(self, row):
        ids, mx = row["ids"], self.mx
        if len(ids) > self.max_row:  # vLLM's engine is capped at max_row the same way
            raise ValueError("row exceeds the checkpoint's row limit")
        # Kai's pointer head reads the output of mlx-lm's Qwen3.5 text body,
        # lm.language_model.model (Kai train/kai/qwen/forward.py:29), called causally on whole
        # rows with no cache in the rows form (forward.py:44, backbone.py:169) and cast to FP32
        # (forward.py:56): the last decoder layer after the final RMSNorm, before any LM head.
        # One row per forward, a batch of one, no prompt cache: attention and DeltaNet's
        # recurrent state see only this question's row.
        h = self.text(mx.array([ids], dtype=mx.int32), cache=None)
        h = np.array(h.astype(mx.float32))
        if h.shape != (1, len(ids), self.width):
            raise ValueError("backbone returned incomplete hidden states")
        if not np.isfinite(h).all():
            raise ValueError("nonfinite hidden states")
        return h[0]


def wire_lora(lm, package, mx):
    """The package's adapter on a loaded base: mlx-lm's LoRALinear (no dropout, scale alpha over
    rank) around every dense projection of a decoder layer that carries a target name, with the
    PEFT factors A [rank, inputs] and B [outputs, rank] as lora_a = A^T and lora_b = B^T in FP32.

    Strict in both directions, and decided before a factor is read: the projections found are
    exactly the adapter's (package.adapter, from the file's header), each with the adapter's
    dimensions. Returns the number of projections adapted."""
    import mlx.nn as nn
    from mlx.utils import tree_unflatten
    from mlx_lm.tuner.lora import LoRALinear

    from .package import ADAPTER, MLX_PREFIX, PEFT_PREFIX

    lora, found = package.meta["lora"], {}
    for name, module in lm.named_modules():
        if name.startswith(MLX_PREFIX + "layers.") and name.rsplit(".", 1)[-1] in lora["targets"]:
            if type(module) is not nn.Linear:
                raise ValueError("a module under a LoRA target name is not a dense projection")
            found[name] = module
    if set(found) != set(package.adapter):
        raise ValueError("the adapter's projections are not the base's target projections")
    for name, module in found.items():
        if tuple(module.weight.shape) != package.adapter[name][::-1]:
            raise ValueError("an adapter factor does not fit its projection")
    factors = mx.load(str(package.root / ADAPTER))
    swaps = []
    for name, module in found.items():
        stem = PEFT_PREFIX + name.removeprefix(MLX_PREFIX)
        wrapped = LoRALinear.from_base(
            module, r=lora["rank"], dropout=0.0, scale=lora["alpha"] / lora["rank"]
        )
        a = factors.pop(stem + ".lora_A.weight").astype(mx.float32).T
        b = factors.pop(stem + ".lora_B.weight").astype(mx.float32).T
        if a.shape != wrapped.lora_a.shape or b.shape != wrapped.lora_b.shape:
            raise ValueError("an adapter factor does not fit its projection")
        wrapped.lora_a, wrapped.lora_b = a, b
        swaps.append((name, wrapped))
    if factors:
        raise ValueError("adapter tensors without a projection")
    lm.update_modules(tree_unflatten(swaps))
    return len(swaps)


def delta_update(package):
    """(mlx-lm's qwen3_5 module, this package's gated-delta update), once it is certain that
    putting the second into the first gives the program the package pins. Refused when a module
    in use was imported from another file than the one whose hash the package verified, or when
    the text body's update is already something other than mlx-lm's own or this one: another
    substitution is never stacked on, replaced or left half in place."""
    from mlx_lm.models import gated_delta as reference
    from mlx_lm.models import qwen3_5

    from . import delta

    def read_from(module, path):
        return path is not None and Path(module.__file__).resolve() == Path(path).resolve()

    pinned = package.runtime
    if not (
        read_from(qwen3_5, pinned["sources"]["qwen3_5.py"])
        and read_from(reference, pinned["sources"]["gated_delta.py"])
        and read_from(delta, pinned["delta"])
    ):
        raise ValueError("the imported runtime is not the one the package's hashes were taken of")
    current = qwen3_5.gated_delta_update
    if current is not delta.gated_delta_update and current is not reference.gated_delta_update:
        raise ValueError("another gated-delta update is installed in this process")
    return qwen3_5, delta.gated_delta_update


class MlxLoraBackbone(MlxBackbone):
    """The same whole-row forward over an unmerged package (package.LoraPackage): the base as
    stored, the run's LoRA beside it, the text body in training mode and this package's chunked
    DeltaNet update in mlx-lm's place: the numerical program Kai's training scorer ran.

    The update is put in place for the life of the process, by one assignment, after everything
    else has succeeded. It serves only a text body in training mode (delta.gated_delta_update):
    a merged package in the same process, whose body is in eval mode, is answered as before.
    Every forward first checks that the update and the mode are still in place.

    CPU tests pass ``text`` and ``mx`` as for MlxBackbone.
    """

    name = "mlx-lora"

    def __init__(self, package, *, text=None, mx=None):
        self.width, self.max_row = package.hidden_size, package.meta["max_row"]
        self.in_place = None
        if text is None:
            from .package import refuse_model_code

            refuse_model_code(read_json((package.model_dir / "config.json").read_bytes()))
            import mlx.core as mx
            from mlx.utils import tree_flatten
            from mlx_lm.utils import load_model

            delta_update(package)  # refuse a runtime that cannot be made the pinned one, first
            mx.set_cache_limit(MLX_CACHE_BYTES)  # before the first allocation
            lm = load_model(package.model_dir)[0]  # the verified directory only; no hub access
            lm.freeze()
            wire_lora(lm, package, mx)
            # The base as stored (BF16, FP32 where it keeps it) and the adapter in FP32, as it
            # was trained. Any other number type, float16 above all, is refused.
            if any(
                p.dtype not in (mx.bfloat16, mx.float32) for _, p in tree_flatten(lm.parameters())
            ):
                raise ValueError(
                    "MLX backbone requires bfloat16 weights (float32 where the base keeps it)"
                )
            mx.eval(lm.parameters())
            text = lm.language_model.model
            text.train()  # mlx-lm's text body then calls the update without its kernel
            qwen3_5, update = delta_update(package)
            qwen3_5.gated_delta_update = update
            self.in_place = lambda: qwen3_5.gated_delta_update is update and text.training
        self.text, self.mx = text, mx

    def hidden(self, row):
        if self.in_place is not None and not self.in_place():
            # Not a caller's fault: the process no longer runs the package's program.
            raise RuntimeError("the package's runtime is no longer in place")
        return super().hidden(row)


# The evidence readouts this build can run: {name: class}. A class is made with (tensors,
# package) from the package's evidence head file and has spans(hidden, row, offsets, state):
# hidden is the whole row's raw states, row its layout ("ids", "decide", "opts"), offsets the
# state rows' places in state (encode.state_offsets), and the result a list of [start, end]
# code-point intervals of state. One is published: Kai's span-pair scorer and decoder
# (pairs.py). No trained head is published with it, and a package that names a readout this
# build does not have is refused, not answered without.
EVIDENCE = {PairReadout.name: PairReadout}
MAX_SPANS, MAX_EVIDENCE_STATE = 32, 4096


def evidence_head(package):
    """(the package's evidence readout made from its head file, the head's SHA-256), or
    (None, None) for a package without one. The readout's source must be the one the
    package pins, as the DeltaNet update's must."""
    told = getattr(package, "evidence", None)
    if told is None:
        return None, None
    import inspect

    from safetensors.numpy import load_file

    from .package import EVIDENCE_HEAD, sha256

    readout = EVIDENCE.get(told["readout"])
    if readout is None:
        raise ValueError(f"no evidence readout {told['readout']!r} in this build")
    if sha256(Path(inspect.getsourcefile(readout))) != told["impl_sha256"]:
        raise ValueError("this evidence readout is not the source the package pins")
    if package.meta["max_state"] > MAX_EVIDENCE_STATE:
        raise ValueError("an evidence head reads at most 4,096 state tokens")
    head = package.root / EVIDENCE_HEAD
    return readout(load_file(head), package), package.meta["files"][EVIDENCE_HEAD]


def checked_spans(spans, length):
    """spans as an answer may carry them, or None: integer pairs inside the state, in order,
    none overlapping the one before, at most MAX_SPANS."""
    if not isinstance(spans, list) or len(spans) > MAX_SPANS:
        return None
    out, end = [], 0
    for span in spans:
        pair = isinstance(span, (list, tuple)) and len(span) == 2
        if (
            not pair
            or any(type(x) is not int for x in span)
            or not end <= span[0] < span[1] <= length
        ):
            return None
        out.append([span[0], span[1]])
        end = span[1]
    return out


class Service:
    def __init__(self, package, tokenizer, pointer, backbone, source=None):
        self.package, self.tok, self.pointer, self.backbone = package, tokenizer, pointer, backbone
        self.source = source or {}
        self.evidence, self.head_sha256 = None, None  # set by load_service for a package's head
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
            "backend": self.backbone.name,  # each backbone names itself: vllm or mlx
            "model": "kai-4b",
            "readout": "pointer",
            "policy_ready": False,
            # True only with a head loaded. It says spans are computed, not that they are
            # right: no evidence head is qualified.
            "evidence_available": self.evidence is not None,
            "package_sha256": self.package.identity,
            "diagnostic": True,
            # Said only by a package that is not a merged export (LoraPackage: "lora-unmerged").
            **({"weights": w} if (w := getattr(self.package, "weights", None)) else {}),
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
        probs, marked, offsets = [], [], None
        if self.evidence is not None:
            try:
                offsets = state_offsets(self.tok, rec["state"], state[1:])
            except ValueError:  # no places for this state: its evidence is an error, said so
                offsets = ()
        for r in branches:
            row = {
                "ids": state + r["ids"],
                "decide": len(state) + r["decide"],
                "opts": [len(state) + p for p in r["opts"]],
            }
            hidden = self.backbone.hidden(row)
            logits = self.pointer.logits(hidden, row).astype(np.float64)
            if offsets is not None:
                marked.append(self._spans(hidden, row, offsets, rec["state"]))
            logits = (logits - logits.max()) / m["temperature"]
            p = np.exp(logits)
            p /= p.sum()
            if not np.isfinite(p).all():
                raise ValueError("nonfinite probabilities")
            probs.append(p.tolist())
        answers = to_answers(probs, meta)
        if offsets is None:
            return {**self.metadata(), "answers": answers, "coverage": "complete"}
        # Evidence rides beside each answer and changes none: which state it is of, which
        # head marked it, and for each question spans, none, or that marking failed.
        for item, found in zip(meta, marked, strict=True):
            answers[item["id"]]["evidence"] = found
        text = rec["state"]
        about = {"state_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        about |= {"state_chars": len(text), "head_sha256": self.head_sha256}
        about |= {"readout": self.package.evidence["readout"], "qualified": False}
        return {**self.metadata(), "answers": answers, "evidence": about, "coverage": "complete"}

    def _spans(self, hidden, row, offsets, text):
        """One question's evidence: {"status", "spans"}. A head that fails, or whose
        intervals are not intervals of this state, is an error for this question alone;
        the answer beside it was already computed and stands."""
        try:
            found = self.evidence.spans(hidden, row, offsets, text) if offsets else None
        except Exception:  # noqa: BLE001 - whatever a head raises, the answer is not its to lose
            found = None
        spans = checked_spans(found, len(text))
        if spans is None:
            return {"status": "error", "spans": []}
        return {"status": "spans" if spans else "empty", "spans": spans}


def load_service(
    directory, *, hf_blobs=None, source=None, backend="vllm", base_dir=None, allocate=True
):
    """A Service for a package directory of either schema (package.py). The package, then its
    tokenizer and pointer, are verified before a backbone is built.

    base_dir is where an unmerged package's base is (default: the local Hugging Face cache); a
    merged export carries its own weights and does not use it. allocate=False stops after the
    verification: the returned service has no backbone and cannot answer."""
    if backend not in ("vllm", "mlx"):
        raise ValueError("unknown serving backend")
    from safetensors.numpy import load_file
    from transformers import AutoTokenizer

    from .package import LORA_SCHEMA, LoraPackage, Package, schema_of

    if schema_of(directory) == LORA_SCHEMA:
        if backend != "mlx" or hf_blobs is not None:
            raise ValueError("an unmerged package is a local one, answered by the MLX backend")
        package, build = LoraPackage(directory, base_dir=base_dir), MlxLoraBackbone
    else:
        package = Package(directory, hf_blobs=hf_blobs)
        build = MlxBackbone if backend == "mlx" else VllmBackbone
    tokenizer = AutoTokenizer.from_pretrained(
        package.model_dir, local_files_only=True, trust_remote_code=False
    )
    pointer = Pointer(load_file(package.root / "pointer.safetensors"), package.hidden_size)
    # Validate tokenizer/head before allocating any backbone.
    service = Service(package, tokenizer, pointer, None, source=source)
    service.evidence, service.head_sha256 = evidence_head(package)
    if allocate:
        service.backbone = build(package)
    return service
