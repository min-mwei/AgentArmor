"""Write the manifest of a fine-tuned model's directory from the receipt of its run.

    python training/summarizer/bundle_manifest.py training/summarizer/runs/trial-003a

A run leaves receipt.json beside model/, with the hash of every file the model directory
held when the run ended. This writes model/bundle.jsonl (summarizer.bundle), which the
runtime needs before it admits the directory: the manifest names every file, the probe set,
what the model turns out to be once loaded, and the numbers it is to be served under.

It is refused when the directory is no longer what the receipt lists: a manifest must not
bless files the run did not leave. Nothing is trained, calibrated or scored. The model is
loaded once, to read its dimensions, prompts and window.

A manifest says what a bundle is and not that it is good. The operating numbers it lists
are the runtime's own at the time of writing; that they suit this model is for a
measurement to say, and a measurement names the manifest it measured.
"""

import argparse
import hashlib
import json
from pathlib import Path

from summarizer import bundle
from summarizer.probes import load as load_probes


def manifest(run, load=None, replace=False):
    """(name, path) of the manifest written for the model of a run directory.

    load(directory) gives an embedder for the model there; the runtime's own by default.
    """
    from summarizer.summarize import operating

    run = Path(run)
    directory = run / "model"
    raw = (run / "receipt.json").read_bytes()
    receipt = json.loads(raw)
    if (directory / bundle.NAME).exists() and not replace:
        raise SystemExit(f"{directory} has a manifest already; --replace writes another")
    listed, found = receipt["outputs"]["model"]["files"], bundle.files(directory)
    odd = sorted(path for path in {*found, *listed} if found.get(path) != listed.get(path))
    if odd:
        raise SystemExit(
            f"{directory} is not what its run left: {len(odd)} of its files differ from the "
            f"receipt ({', '.join(odd[:3])}). No manifest is written for it."
        )
    if load is None:
        from summarizer.embedder import Embedder as load
    embedder = load(str(directory))
    version, _ = load_probes(str(directory))
    base = receipt.get("base") or {}
    source = {"base": base.get("model"), "revision": base.get("revision")}
    source |= {"receipt_sha256": hashlib.sha256(raw).hexdigest(), "code": receipt.get("code")}
    record = bundle.make(directory, embedder.found(), version, operating(), source)
    return bundle.name(record), bundle.write(directory, record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run", help="a run directory: receipt.json and model/")
    parser.add_argument("--device", help="cpu, mps or cuda; detected when not given")
    parser.add_argument("--replace", action="store_true", help="write over a manifest")
    args = parser.parse_args(argv)

    def load(directory):
        from summarizer.embedder import Embedder

        return Embedder(directory, device=args.device, local_files_only=True)

    name, path = manifest(args.run, load, args.replace)
    print(json.dumps({"bundle": name, "manifest": str(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
