"""A local model directory, named by what is in it.

The pinned base model is named by its revision on the hub. A directory has no such name. It
is a path, and a fine-tune saved over another, a tokenizer edited by hand or a probes.jsonl
copied in from another run all sit at the same one. A manifest gives a directory a name that
changes when any of that changes. It is one record, in bundle.jsonl beside the files:

    files          every other file of the directory by SHA-256: the weights, the tokenizer,
                   the prompts' configuration, and the probes with their calibration
    probes         the version of that probe set, as probes.load() names it
    dim, prompts, policy, window_tokens, max_windows, batch
                   what the model, once loaded, must turn out to be and be served under
    operating      the numbers the runtime applies around the model (summarize.operating())
    source         where the bundle came from: its base model and the receipt of its run

The runtime admits a directory when all of that holds: the files hash to what is listed, the
loaded model has those dimensions, prompts, policy and window, the probes are the ones
listed, and the operating numbers are the ones in use. Its identity is then the manifest's
own hash. A directory that is not admitted is still served, as "local": nothing binds its
probes' numbers to its weights, so the numbers are dropped, no probe label is given, and the
runtime says why.

A manifest says what a bundle is. It does not say the bundle is good: that is a measurement,
and a measurement names the manifest it measured.

Standard library only.
"""

import hashlib
import json
from pathlib import Path

NAME = "bundle.jsonl"
VERSION = 1
SERVED = ("dim", "prompts", "policy", "window_tokens", "max_windows", "batch")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def files(directory):
    """{path within the directory: SHA-256} for every file in it but the manifest."""
    root = Path(directory)
    manifest = root / NAME
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != manifest
    }


def whole(listed):
    """One hash over a files() listing."""
    return hashlib.sha256(json.dumps(listed, sort_keys=True).encode()).hexdigest()


def name(record):
    """The name of a bundle: the hash of its manifest."""
    return hashlib.sha256(_canonical(record).encode("ascii")).hexdigest()


def make(directory, served, probes, operating, source):
    """The manifest of a directory as it is now. served holds what was found on the loaded
    model (SERVED), probes the version of its probe set, operating the runtime's numbers."""
    record = {"manifest": VERSION, "files": files(directory), "probes": probes}
    record |= {key: served[key] for key in SERVED}
    return record | {"operating": operating, "source": source}


def write(directory, record):
    path = Path(directory) / NAME
    path.write_text(_canonical(record) + "\n", encoding="ascii")
    return path


def read(directory):
    """(manifest, None) when the directory is what its manifest lists; else (None, why)."""
    root = Path(directory)
    try:
        lines = (root / NAME).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None, f"the model directory has no manifest ({NAME})"
    except (OSError, UnicodeDecodeError):
        return None, "its manifest cannot be read"
    try:
        (record,) = [json.loads(line) for line in lines if line.strip()]
    except ValueError:
        return None, "its manifest is not one JSON record"
    needed = ("files", "probes", "operating", "source", *SERVED)
    if not isinstance(record, dict) or record.get("manifest") != VERSION:
        return None, f"its manifest is not of version {VERSION}"
    if any(key not in record for key in needed) or not isinstance(record["files"], dict):
        return None, "its manifest is incomplete"
    try:
        found = files(root)
    except OSError:
        return None, "its files cannot be read"
    listed = record["files"]
    odd = sorted(path for path in {*found, *listed} if found.get(path) != listed.get(path))
    if odd:
        files_differ = "1 file differs" if len(odd) == 1 else f"{len(odd)} files differ"
        more = "" if len(odd) <= 3 else f" and {len(odd) - 3} more"
        return None, f"{files_differ} from its manifest: {', '.join(odd[:3])}{more}"
    return record, None


def unlike(record, served):
    """Why what is served is not what a manifest lists, or None. served maps names of the
    manifest (SERVED, "probes", "operating") to what is in fact loaded and in use; only the
    names given are compared."""
    for key in (*SERVED, "probes", "operating"):
        if key in served and served[key] != record[key]:
            if isinstance(record[key], dict):
                what = "operating numbers" if key == "operating" else key
                return f"its manifest lists other {what} than are in use"
            return f"its manifest lists {key} {record[key]!r}, and {served[key]!r} is in use"
    return None
