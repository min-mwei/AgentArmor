"""Bind the trainer-selected Kai serving contract without loading a model."""

import argparse
import json
import os
from pathlib import Path

from armor.kai import KaiPackage, file_sha, runtime_identity


def section(directory, root, required):
    directory = Path(directory).expanduser().resolve()
    return {
        "path": os.path.relpath(directory, root),
        "files_sha256": {name: file_sha(directory / name) for name in required},
    }


def build(base, toolcall, xpia, contract, out):
    """Contract is supplied by the trainer; this helper never chooses or fits a cutoff."""
    out = Path(out).expanduser().resolve()
    if out.exists():
        raise FileExistsError("Kai package destination already exists")
    contracts = json.loads(Path(contract).read_text(encoding="utf-8"))
    base = Path(base).expanduser().resolve()
    names = ["model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json"]
    names.extend(
        name
        for name in (
            "special_tokens_map.json",
            "added_tokens.json",
            "tokenizer.model",
            "spiece.model",
            "sentencepiece.bpe.model",
        )
        if (base / name).is_file()
    )
    manifest = {
        "format": "kai-encoder-v1",
        "runtime": runtime_identity(),
        "base": section(base, out, names),
        "branches": {},
    }
    for kind, path in (("toolcall", toolcall), ("xpia", xpia)):
        manifest["branches"][kind] = {
            **contracts[kind],
            **section(path, out, ["meta.json", "weights.safetensors"]),
        }
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return KaiPackage(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--toolcall-run", type=Path, required=True)
    parser.add_argument("--xpia-run", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    package = build(args.base, args.toolcall_run, args.xpia_run, args.contract, args.out)
    print(json.dumps({"package_sha256": package.sha256, "model_loaded": False}))


if __name__ == "__main__":
    main()
