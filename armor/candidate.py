"""Explicit opt-in to a pinned experimental Kai package, without loading a model."""

import hashlib
import json
import math
from pathlib import Path

from armor.v0 import contract, verified_json


def load_contract(directory: str | Path | None = None):
    if directory is None:
        return contract()[0]
    directory = Path(directory).expanduser().resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    cutoffs = verified_json(directory / "cutoffs.json", manifest["cutoffs"]["sha256"])
    if cutoffs.get("standard") not in ("v2.1", "v2.2"):
        raise ValueError("Candidate package must name its policy standard")
    for name in ("toolcall", "bipia"):
        value = cutoffs[name]["cutoff"]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"Invalid candidate {name} cutoff")
    if not 0 <= cutoffs["toolcall"]["cutoff"] <= 1:
        raise ValueError("ToolCall cutoff is outside [0, 1]")
    return cutoffs


def verify_snapshot(directory: str | Path, snapshot: Path):
    """Bind a local adapter or merged export to the same manifest as its cutoffs."""
    directory = Path(directory).expanduser().resolve()
    load_contract(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    candidates = [
        manifest.get("checkpoint", {}).get("files_sha256", {}),
        manifest.get("serving", {}).get("merged_bf16", {}).get("files_sha256", {}),
    ]
    required = {"head.pt", "tokenizer.json", "tokenizer_config.json"}
    if (snapshot / "evidence_head.safetensors").exists():
        required.add("evidence_head.safetensors")
    for pins in candidates:
        if not required <= pins.keys():
            continue
        if not (
            {"model.safetensors", "config.json"} <= pins.keys()
            or {"adapter_model.safetensors", "adapter_config.json"} <= pins.keys()
        ):
            continue
        matched = True
        for name, pin in pins.items():
            if Path(name).name != name:
                raise ValueError("Candidate file pins must use plain filenames")
            path = snapshot / name
            if not path.is_file():
                matched = False
                break
            with path.open("rb") as stream:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
                digest = hasher.hexdigest()
            if digest != pin:
                matched = False
                break
        if matched:
            return
    raise ValueError("Model snapshot does not match the candidate package's file pins")


def scan_cutoffs(cutoffs: dict, profile: str):
    if profile == "xpia-document-v0":
        return cutoffs
    section = cutoffs.get("xpia_context") or {}
    if section.get("profile") != profile:
        raise ValueError("Contextual scans require a cutoff registered for xpia-context-v1")
    value = section.get("cutoff")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Contextual scan cutoff must be finite")
    result = {"bipia": {"cutoff": value}}
    if "xpia_question" in cutoffs:
        result["xpia_question"] = cutoffs["xpia_question"]
    return result
