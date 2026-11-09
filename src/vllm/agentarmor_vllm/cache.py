"""Resolve a pinned Kai export in the standard Hugging Face cache."""

import re
from pathlib import Path

KAI_REPO = "min-h-wei/Kai-4B"


def kai_snapshot(revision, cache_dir=None, local_files_only=False):
    """Fetch a trainer-published vLLM export, never the unmerged training checkpoint."""
    from huggingface_hub import snapshot_download

    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Kai requires a full HF commit revision")
    root = Path(
        snapshot_download(
            KAI_REPO,
            revision=revision,
            allow_patterns=["vllm/*"],
            cache_dir=cache_dir,
            local_files_only=local_files_only,
            token=False,
        )
    ).resolve(strict=True)
    if root.name != revision or root.parent.name != "snapshots":
        raise ValueError("unexpected Hugging Face snapshot layout")
    package = root / "vllm"
    if not (package / "kai.json").is_file():
        raise ValueError("Kai revision has no trainer-published vllm/kai.json export")
    return package, root.parent.parent / "blobs"
