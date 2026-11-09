"""Read a local, hash-bound merged export; never unpickle a training checkpoint."""

import hashlib
import math
from pathlib import Path

from .wire import read_json

BASE_REVISION = "1001bb4d826a52d1f399e183466143f4da7b741b"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class Package:
    def __init__(self, directory, hf_blobs=None):
        self.root = Path(directory).resolve(strict=True)
        blob_root = Path(hf_blobs).resolve(strict=True) if hf_blobs is not None else None
        self.meta = m = read_json((self.root / "kai.json").read_bytes())
        required = {
            "schema",
            "base",
            "base_revision",
            "temperature",
            "max_state",
            "max_row",
            "max_packed",
            "pauses",
            "pause_id",
            "tokenizer_sha256",
            "files",
            "dtype",
            "evidence",
            "weights",
        }
        if set(m) != required or m["schema"] != "agentarmor-qwen/v1":
            raise ValueError("unsupported package schema")
        if m["base"] != "Qwen/Qwen3.5-4B-Base" or m["base_revision"] != BASE_REVISION:
            raise ValueError("package must identify the registered Qwen3.5-4B base")
        if m["weights"] != "merged" or m["dtype"] != "bfloat16":
            raise ValueError("baseline requires a merged BF16 HF export")
        if m["evidence"] != "frozen-from-pointer":
            raise ValueError("unknown evidence qualification")
        if (
            type(m["temperature"]) not in (int, float)
            or not math.isfinite(m["temperature"])
            or m["temperature"] <= 0
        ):
            raise ValueError("invalid checkpoint temperature")
        for key in ("max_state", "max_row", "max_packed"):
            if type(m[key]) is not int or not 1 <= m[key] <= 65536:
                raise ValueError("invalid context limit")
        if not m["max_state"] <= m["max_row"] <= m["max_packed"]:
            raise ValueError("inconsistent context limits")
        if type(m["pauses"]) is not int or not 0 <= m["pauses"] <= 1024:
            raise ValueError("invalid pause count")
        if m["pauses"] and (type(m["pause_id"]) is not int or m["pause_id"] < 0):
            raise ValueError("invalid pause token")
        if not m["pauses"] and m["pause_id"] is not None:
            raise ValueError("unexpected pause token")
        # Bind every runtime artifact, including tokenizer/config and all weight shards.
        actual = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()}
        if not isinstance(m["files"], dict) or set(m["files"]) != actual - {"kai.json"}:
            raise ValueError("manifest must bind every package file")
        required_files = {"model/config.json", "model/tokenizer.json", "pointer.safetensors"}
        if not required_files <= set(m["files"]):
            raise ValueError("missing model/tokenizer/pointer files")
        if not any(p.startswith("model/") and p.endswith(".safetensors") for p in m["files"]):
            raise ValueError("missing merged model weights")
        for name, digest in m["files"].items():
            path = (self.root / name).resolve(strict=True)
            # The HF cache uses symlinks to content-addressed files in its adjacent blob dir.
            # Local exports remain confined to their own root; only the HF loader opts in.
            in_package = path.is_relative_to(self.root)
            # Newer hubs link repo blobs onward into the cache-level shared store
            # (<cache>/blobs/ab/<hash>); accept only those two cache directories.
            in_blobs = blob_root is not None and (
                path.is_relative_to(blob_root)
                or path.is_relative_to(blob_root.parent.parent / "blobs")
            )
            if not (in_package or in_blobs) or sha256(path) != digest:
                raise ValueError("package artifact hash mismatch")
        config = read_json((self.root / "model/config.json").read_bytes())
        config = config.get("text_config", config)
        if (
            config.get("model_type") != "qwen3_5_text"
            or config.get("hidden_size") != 2560
            or config.get("num_hidden_layers") != 32
        ):
            raise ValueError("unexpected Qwen backbone")
        self.hidden_size = 2560
        self.identity = sha256(self.root / "kai.json")
