"""A local model directory is named by a manifest of what is in it (MR113-3)."""

import hashlib
import json

import pytest

from summarizer import bundle
from summarizer.embedder import HashEmbedder
from summarizer.summarize import Runtime, operating

SERVED = {"dim": 4, "prompts": {"SearchQuery": "q: ", "Document": "d: "}, "policy": "windows/1"}
SERVED |= {"window_tokens": 512, "max_windows": 16, "batch": 1}
PROBE = {"id": "agent_directive", "label": "agent_directive", "weight": 0.9}
PROBE |= {"queries": ["an instruction addressed to an AI agent"]}
PROBE |= {"scale": 6.5, "bias": -1.4, "threshold": 0.04}


def model_directory(root):
    root.mkdir(parents=True)
    (root / "1_Pooling").mkdir()
    (root / "model.safetensors").write_bytes(b"weights " * 300_000)  # more than one block
    (root / "tokenizer.json").write_text('{"model_max_length": 512}')
    (root / "1_Pooling" / "config.json").write_text("{}")
    (root / "probes.jsonl").write_text(json.dumps(PROBE) + "\n")
    return root


def written(root, **changed):
    version = "sha256:" + hashlib.sha256((root / "probes.jsonl").read_bytes()).hexdigest()[:12]
    source = {"base": "google/embeddinggemma-2", "revision": "r", "receipt_sha256": "0" * 64}
    record = bundle.make(root, SERVED | changed, version, operating(), source | {"code": "c"})
    bundle.write(root, record)
    return record


def test_a_manifest_lists_every_file_and_names_the_directory(tmp_path):
    root = model_directory(tmp_path / "model")
    assert bundle.read(root) == (None, "the model directory has no manifest (bundle.jsonl)")
    record = written(root)
    listed = record["files"]
    assert sorted(listed) == ["1_Pooling/config.json", "model.safetensors", "probes.jsonl"] + [
        "tokenizer.json"
    ]
    weights = hashlib.sha256(b"weights " * 300_000).hexdigest()
    assert listed["model.safetensors"] == weights and bundle.NAME not in listed
    # One record on one line, read back as written, and the same name each time.
    lines = (root / bundle.NAME).read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == record
    assert bundle.read(root) == (record, None)
    name = bundle.name(record)
    assert len(name) == 64 and name == bundle.name(json.loads(lines[0]))
    assert name != bundle.name(record | {"window_tokens": 1024})
    # The hash of the files is the same with the manifest beside them as without it.
    assert bundle.files(root) == listed
    assert (
        bundle.whole(listed)
        == hashlib.sha256(json.dumps(listed, sort_keys=True).encode()).hexdigest()
    )


def test_a_directory_that_is_not_what_its_manifest_lists_is_not_read_as_it(tmp_path):
    root = model_directory(tmp_path / "model")
    record = written(root)
    probes = (root / "probes.jsonl").read_text()
    # A probe set copied in from another run: other numbers beside the same weights.
    (root / "probes.jsonl").write_text(probes.replace("0.04", "0.5"))
    assert bundle.read(root) == (None, "1 file differs from its manifest: probes.jsonl")
    (root / "probes.jsonl").write_text(probes)
    assert bundle.read(root) == (record, None)
    # A file that came later, one that went, and many at once.
    (root / "extra.bin").write_bytes(b"x")
    assert bundle.read(root) == (None, "1 file differs from its manifest: extra.bin")
    (root / "tokenizer.json").unlink()
    assert bundle.read(root)[1] == "2 files differ from its manifest: extra.bin, tokenizer.json"
    for n in range(3):
        (root / f"more{n}.bin").write_bytes(b"x")
    assert bundle.read(root)[1] == (
        "5 files differ from its manifest: extra.bin, more0.bin, more1.bin and 2 more"
    )
    # A manifest that is not one, of another version, or with a part missing.
    for text, why in (
        ("not json\n", "its manifest is not one JSON record"),
        ("{}\n{}\n", "its manifest is not one JSON record"),
        (json.dumps(record | {"manifest": 2}) + "\n", "its manifest is not of version 1"),
        (json.dumps({k: v for k, v in record.items() if k != "probes"}) + "\n", "incomplete"),
        (json.dumps([record]) + "\n", "its manifest is not of version 1"),
    ):
        (root / bundle.NAME).write_text(text)
        assert why in bundle.read(root)[1] and bundle.read(root)[0] is None
    (root / bundle.NAME).write_bytes(b"\xff\xfe")
    assert bundle.read(root) == (None, "its manifest cannot be read")


def test_what_is_served_is_held_against_the_manifest(tmp_path):
    record = written(model_directory(tmp_path / "model"))
    assert bundle.unlike(record, SERVED) is None
    assert bundle.unlike(record, SERVED | {"window_tokens": 1024}) == (
        "its manifest lists window_tokens 512, and 1024 is in use"
    )
    assert bundle.unlike(record, SERVED | {"policy": "windows/2"}) == (
        "its manifest lists policy 'windows/1', and 'windows/2' is in use"
    )
    assert bundle.unlike(record, SERVED | {"dim": 768}).startswith("its manifest lists dim 4")
    other = {"SearchQuery": "query: ", "Document": "d: "}
    assert bundle.unlike(record, {"prompts": other}) == (
        "its manifest lists other prompts than are in use"
    )
    assert bundle.unlike(record, {"probes": record["probes"]}) is None  # only what is given


def served_from(root, record, refused=None):
    """A stand-in for the embedder of a model directory: what Embedder leaves on itself."""
    made = HashEmbedder()
    made.info = dict(made.info) | {"model": str(root), "revision": "local", "batch": 1}
    made.bundle, made.unadmitted = record, refused
    return made


def test_the_runtime_admits_a_directory_only_as_its_manifest_lists_it(tmp_path, monkeypatch):
    # MR113-3. A local model was "revision: local" whatever its files were, and the probe
    # numbers found beside it were taken as fitted for it.
    root = model_directory(tmp_path / "model")
    record = written(root)
    admitted = Runtime(served_from(root, record), str(root))
    assert admitted.bundle == bundle.name(record) and admitted.uncalibrated_why is None
    assert admitted.calibrated and [m[0] for m in admitted.matches([1.0])] == ["agent_directive"]
    # No manifest, or files that differ from it: the embedder says why, and no probe marks.
    why = bundle.read(model_directory(tmp_path / "bare"))[1]
    bare = Runtime(served_from(root, None, why), str(root))
    assert bare.bundle is None and bare.calibrated is False and list(bare.matches([1.0])) == []
    assert bare.uncalibrated_why == (
        "the model bundle is not admitted: the model directory has no manifest (bundle.jsonl)"
    )
    assert bare.identity()["embedding"]["calibrated"] is False
    # Probes from somewhere else than the directory the manifest lists.
    elsewhere = tmp_path / "other.jsonl"
    elsewhere.write_text(json.dumps(PROBE | {"threshold": 0.5}) + "\n")
    swapped = Runtime(served_from(root, record), str(elsewhere))
    assert swapped.bundle is None and swapped.calibrated is False
    assert swapped.uncalibrated_why.startswith(
        f"the model bundle is not admitted: its manifest lists probes {record['probes']!r}, and"
    )
    # Operating numbers other than the manifest lists: both uses of the drift margin read
    # the one number, and a bundle is not served under another than it names.
    import summarizer.summarize as runtime

    monkeypatch.setattr(runtime, "DRIFT_MARGIN", 0.08)
    assert operating()["drift"] == {"margin": 0.08, "slope": 2.0, "max": 0.25}
    moved = Runtime(served_from(root, record), str(root))
    assert moved.bundle is None and moved.uncalibrated_why == (
        "the model bundle is not admitted: "
        "its manifest lists other operating numbers than are in use"
    )
    monkeypatch.undo()
    # The pinned base model and the stand-ins have no directory, and nothing changes for them.
    assert Runtime(HashEmbedder()).bundle is None
    assert operating() == {
        "action_chars": 1200,
        "chunk_chars": 320,
        "min_ranked_chars": 32,
        "min_linked_chars": 16,
        "drift": {"margin": 0.05, "slope": 2.0, "max": 0.25},
        "diversity": {"same": 0.97, "weight": 0.7},
    }


def test_a_manifest_is_written_only_for_what_the_run_left(tmp_path):
    import bundle_manifest
    from score_eval import bundle as hashed

    run = tmp_path / "run"
    root = model_directory(run / "model")
    receipt = {"base": {"model": "google/embeddinggemma-2", "revision": "r"}, "code": "c"}
    receipt["outputs"] = {"model": hashed(root)}
    (run / "receipt.json").write_text(json.dumps(receipt))

    class Loaded:
        def found(self):
            return dict(SERVED)

    name, path = bundle_manifest.manifest(run, lambda directory: Loaded())
    record, why = bundle.read(root)
    assert why is None and path == root / bundle.NAME and name == bundle.name(record)
    assert (
        record["source"]["receipt_sha256"]
        == hashlib.sha256((run / "receipt.json").read_bytes()).hexdigest()
    )
    assert record["operating"] == operating() and record["window_tokens"] == 512
    assert record["probes"].startswith("sha256:") and record["source"]["base"]
    # The model's hash in a receipt is the same with the manifest there: it is not counted.
    assert hashed(root) == receipt["outputs"]["model"]
    # Never over a manifest unasked, and never for files the run did not leave.
    with pytest.raises(SystemExit, match="has a manifest already"):
        bundle_manifest.manifest(run, lambda directory: Loaded())
    assert bundle_manifest.manifest(run, lambda directory: Loaded(), replace=True)[0] == name
    (root / "probes.jsonl").write_text(json.dumps(PROBE | {"threshold": 0.9}) + "\n")
    with pytest.raises(SystemExit, match="is not what its run left: 1 of its files differ"):
        bundle_manifest.manifest(run, lambda directory: Loaded(), replace=True)
