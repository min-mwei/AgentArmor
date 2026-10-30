"""The official Kai encoder package and its lazy MLX runtime."""

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import threading
from pathlib import Path

from armor.schemas import ChoiceQuestion, NoulQuestion


def file_sha(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def pinned_directory(root, section, required):
    directory = (root / section["path"]).resolve()
    pins = section["files_sha256"]
    if not required <= pins.keys():
        raise ValueError("Kai package is missing required file pins")
    for name, expected in pins.items():
        if Path(name).name != name or file_sha(directory / name) != expected:
            raise ValueError("Kai package file does not match its pin")
    return directory


RUNTIME_PACKAGES = ("kai", "mlx", "mlx-lm", "transformers", "numpy")
MODEL_MODULES = ("api.py", "encode.py", "decision.py", "model.py", "heads.py")


def runtime_identity():
    spec = importlib.util.find_spec("kai")
    if spec is None or spec.origin is None:
        raise RuntimeError("Install the official Kai encoder package")
    source = Path(spec.origin).parent
    return {
        "versions": {name: importlib.metadata.version(name) for name in RUNTIME_PACKAGES},
        "code_sha256": {name: file_sha(source / name) for name in MODEL_MODULES},
    }


class KaiPackage:
    """CPU-only verification. Questions, cutoffs and weights belong to one manifest."""

    def __init__(self, directory, expected_sha=None):
        if not directory:
            raise ValueError("Set ARMOR_CANDIDATE_PACKAGE to a registered Kai encoder package")
        self.root = Path(directory).expanduser().resolve()
        self.sha256 = file_sha(self.root / "manifest.json")
        if expected_sha is not None and self.sha256 != expected_sha:
            raise ValueError("Kai package manifest does not match ARMOR_PACKAGE_SHA256")
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("format") != "kai-encoder-v1":
            raise ValueError("The official Kai requires a kai-encoder-v1 package")
        self.base = pinned_directory(
            self.root,
            self.manifest["base"],
            {"model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json"},
        )
        runtime = self.manifest["runtime"]
        if set(runtime["versions"]) != set(RUNTIME_PACKAGES) or set(runtime["code_sha256"]) != set(
            MODEL_MODULES
        ):
            raise ValueError("Kai package must bind numerical runtime versions and model code")
        self.branches = self.manifest["branches"]
        if set(self.branches) != {"toolcall", "xpia"}:
            raise ValueError("Kai package requires toolcall and xpia branches")
        self.paths = {}
        for kind, branch in self.branches.items():
            self.paths[kind] = pinned_directory(
                self.root, branch, {"meta.json", "weights.safetensors"}
            )
            cutoff = branch["cutoff"]
            if type(cutoff) not in (float, int) or not math.isfinite(cutoff) or not 0 < cutoff < 1:
                raise ValueError("Kai branch cutoff must be a finite probability inside (0, 1)")
            schema = ChoiceQuestion if kind == "toolcall" else NoulQuestion
            question = schema.model_validate(branch["question"])
            if kind == "toolcall" and set(question.criteria) != {"allow", "deny"}:
                raise ValueError("Kai ToolCall must use the registered allow/deny direct question")
            threshold = branch.get("evidence_threshold", 0.0)
            if type(threshold) not in (int, float) or not math.isfinite(threshold):
                raise ValueError("Kai evidence threshold must be finite")
        if self.branches["toolcall"]["profile"] != "armor-toolcall-v1":
            raise ValueError("Unknown Kai ToolCall renderer profile")
        profile = self.branches["xpia"]["profile"]
        if profile not in ("xpia-document-v0", "xpia-context-v1"):
            raise ValueError("Unknown Kai scan renderer profile")
        xp = self.branches["xpia"]["cutoff"]
        self.cutoffs = {
            "standard": "kai-direct-v1",
            "toolcall": {"cutoff": self.branches["toolcall"]["cutoff"]},
            "bipia": {"cutoff": math.log(xp / (1 - xp))},
            "xpia_question": self.branches["xpia"]["question"],
        }
        if profile == "xpia-context-v1":
            self.cutoffs["xpia_context"] = {
                "profile": profile,
                "cutoff": self.cutoffs["bipia"]["cutoff"],
            }


class KaiBackend:
    """Canonical encoder, pointer and evidence heads, with serialized model execution."""

    def __init__(self, settings, package=None):
        self.package = package or KaiPackage(settings.candidate_package, settings.package_sha256)
        if not os.environ.get("KAI_GUARDED"):
            raise RuntimeError(
                "Launch the Kai service through python -m kai.guard run LOG -- COMMAND"
            )
        if runtime_identity() != self.package.manifest["runtime"]:
            raise RuntimeError("Kai model code or dependencies differ from the registered package")
        try:
            import mlx.core as mx
            from kai import api, decision, encode
        except ImportError as error:
            raise RuntimeError(
                "Install the official Kai repository in this Apple Silicon environment"
            ) from error
        self.mx, self.api, self.decision, self.encode = mx, api, decision, encode
        self.settings, self.lock = settings, threading.Lock()
        self.tokenizer = encode.load_tokenizer(self.package.base)
        self.models, self.metadata = {}, {}
        for kind, path in self.package.paths.items():
            model, _, meta = decision.restore(path, snapshot=self.package.base, dtype=mx.bfloat16)
            if meta.get("mask", "prefix") != "prefix" or meta.get("ground"):
                raise ValueError(
                    "This serving profile requires a prefix mask and unchanged state text"
                )
            model.eval()
            self.models[kind], self.metadata[kind] = model, meta
        self.evidence_available = all(m.evidence is not None for m in self.models.values())

    def decide(self, request):
        is_xpia = "is_xpia_attempt" in request.questions
        if is_xpia and len(request.questions) != 1:
            raise ValueError("Do not mix routed XPIA and ToolCall questions in one request")
        kind = "xpia" if is_xpia else "toolcall"
        model, meta = self.models[kind], self.metadata[kind]
        record, questions = self.api.to_record(
            self.api.SystemOneRequest.model_validate(request.model_dump())
        )
        encoded = self.encode.encode(
            self.tokenizer,
            record,
            max_state=meta.get("max_state", self.encode.MAX_STATE),
            max_branch=meta.get("max_branch", self.encode.MAX_BRANCH),
            strict=True,
        )
        if len(encoded) > self.settings.max_model_len:
            raise ValueError("Kai request exceeds the packed token budget; no input was truncated")
        with self.lock:
            logits, evidence = model([encoded], with_evidence=True)
            probabilities = [self.mx.softmax(row) for row in logits[0]]
            self.mx.eval(probabilities, evidence)
            answers = self.api.to_answers([p.tolist() for p in probabilities], questions)
            for index, question in enumerate(questions):
                answer = answers[question["id"]]
                if answer["type"] == "noul":
                    answer["probabilities"] = {"false": 1 - answer["noul"], "true": answer["noul"]}
                spans = []
                if model.evidence is not None:
                    spans = self.decision.spans_from_logits(
                        evidence[0][index],
                        encoded,
                        threshold=self.package.branches[kind].get("evidence_threshold", 0.0),
                    )
                answer["evidence_spans"] = spans
            self.mx.clear_cache()
        return {
            "answers": answers,
            "usage": {"input_tokens": len(encoded), "output_tokens": 0},
            "evidence_available": model.evidence is not None,
            "evidence_status": "diagnostic" if model.evidence is not None else "unavailable",
            "package_sha256": self.package.sha256,
        }
