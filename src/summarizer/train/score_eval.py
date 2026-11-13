"""Score the probes on an evaluation file, along the path the summarizer serves.

    python train/score_eval.py train/data/eval/pilot-001.jsonl --use development \\
        --model google/embeddinggemma-2 --fills 3

Each example's template is filled with invented entities, redacted and cut into chunks exactly
as the runtime does, and every chunk is compared with every probe. Per facet it reports

- the example rate: an example is tagged when any of its chunks reaches the probe's threshold.
  Only chunks long enough for the runtime to rank take part, in the tag and in every ranking
  measure; an example with none is untagged, ranks last, and is counted under unranked.
  Precision, recall, false-positive rate and AUC (on the best chunk's similarity) are computed
  against the example's annotation, which was made on the whole text;
- evidence coverage, on the examples where the facet is present: how often a tagged chunk
  overlaps the annotated span, and how often the best-scoring chunk does. This says whether the
  tag landed where the evidence is. It is not chunk precision or recall: a chunk that overlaps
  the span may hold only part of what makes the facet (the address in one chunk, the order in
  the next), and no chunk here was labelled on its own;
- split evidence: the examples whose span does not sit inside one chunk, counted and scored as
  a slice of their own.

The annotated span is carried from the template to the served text through the recorded
replacements, never by searching for its words. An example whose served text was changed by
redaction has no such map: it is still scored as an example and is counted under unmapped.
Filled variants are repeats of one example, not more examples: a rate is averaged over the
fills first. Rows are given per source block, pooled, as the mean over blocks, and without the
largest block; the interval resamples whole blocks. Nothing printed holds text or an id.
"""

import argparse
import hashlib
import json
import random
import re
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
from build_dataset import fill
from calibrate import identity
from check_eval import seeds_of
from common import (
    auc,
    load_model,
    load_probes,
    pick_device,
    probe_scores,
)

from summarizer import bundle as manifests
from summarizer.embedder import REVISION
from summarizer.events import chunk_spans
from summarizer.probes import calibrated
from summarizer.redact import redact
from summarizer.summarize import MIN_RANKED_CHARS

VOCABULARY = {"development": "dev", "test": "heldout"}
NO_SCORE = None  # the best similarity of a fill with no piece long enough to be ranked: no number


def read_once(path):
    """(bytes, SHA-256) of a file, read one time, so what is hashed is what is used."""
    data = Path(path).read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def rows_of(data):
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def check_window(window):
    """A (size, stride) that covers every character: 0 < stride <= size. None passes."""
    if window is None:
        return None
    size, stride = window
    if not (isinstance(size, int) and isinstance(stride, int) and 0 < stride <= size):
        raise ValueError(f"window needs 0 < stride <= size, not size {size} and stride {stride}")
    return size, stride


def choose_probes(model, probes=None):
    """(name, probes) as the runtime chooses them: the named file, else the model's own, else
    the built-in set."""
    if probes is None and (Path(model) / "probes.jsonl").is_file():
        probes = model
    return load_probes(probes)


def model_identity(model_name, model, device):
    """What a result or a fit holds for: revision or file hashes, device and precision."""
    measured = identity(model, pick_device(device), [])
    out = {"model": model_name, "device": measured["device"], "dtype": measured["dtype"]}
    out["inference"] = measured["inference"]  # batch and token limit: part of what was measured
    if Path(model_name).is_dir():
        return out | {"model_files": bundle(model_name)}  # a local model is named by its bytes
    return out | {"revision": REVISION}  # the hub model is loaded at the pinned revision


def bundle(directory):
    """Each file of a local model with its SHA-256, and one hash over them all.

    The directory's manifest (summarizer.bundle) is not one of them: it lists these files,
    so the hash of a model is the same before a manifest is written beside it and after.
    """
    files = manifests.files(directory)
    return {"sha256": manifests.whole(files), "files": files}


def code_version():
    """The commit this code is at, "-dirty" when it has uncommitted changes; "" if unknown."""
    here = Path(__file__).resolve().parent
    done = subprocess.run(
        ["git", "-C", str(here), "describe", "--always", "--dirty", "--abbrev=40"],
        capture_output=True,
        text=True,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def source_identity(use, claimed=None):
    """{"code", "code_from"} for this run, or SystemExit when a test run cannot be identified.

    A checkout speaks for itself: Git's answer is used, a claim that differs from it is refused,
    and a test is never scored from uncommitted code. Only outside a checkout (an archive) is
    the caller's word taken, as a full commit hash, and recorded as the caller's.
    """
    found = code_version()
    if claimed is not None and not re.fullmatch(r"[0-9a-f]{40}", claimed):
        raise SystemExit("--code takes a full 40-character commit hash")
    if found:
        if claimed is not None and claimed != found:
            raise SystemExit(f"--code {claimed} is not this checkout, which is at {found}")
        if use == "test" and found.endswith("-dirty"):
            raise SystemExit("a test is not scored from uncommitted code: commit first")
        return {"code": found, "code_from": "git"}
    if claimed is None and use == "test":
        raise SystemExit("a test run needs an identified source: pass --code outside a checkout")
    return {"code": claimed or "", "code_from": "caller" if claimed else "unknown"}


def carry(span, edits):
    """Where a template span [start, end) lies once the slots at edits have been filled.

    edits are (start, end, value) in the template. An edge inside a slot has no place in the
    result and is an error; check_eval.py rejects such spans before they get here.
    """

    def moved(edge):
        shift = 0
        for start, end, value in edits:
            if end <= edge:
                shift += len(value) - (end - start)
            elif start < edge:
                raise ValueError(f"offset {edge} is inside the slot at [{start}, {end})")
        return edge + shift

    return moved(span[0]), moved(span[1])


def window_spans(text, size, stride):
    """Overlapping pieces of text: one every stride characters, size long, cut at spaces.

    An offline alternative to the runtime's consecutive chunks, so that more instructions fall
    whole inside some piece. Every character of the text lies in at least one window.
    """
    size, stride = check_window((size, stride))
    spans, start, last = [], 0, len(text)
    while start < last:
        while start < last and text[start].isspace():
            start += 1
        if start >= last:
            break
        end = min(start + size, last)
        if end < last and (cut := text.rfind(" ", start + size // 2, end)) > 0:
            end = cut
        spans.append((start, end))
        if end >= last:
            break
        step = start + stride
        back = text.rfind(" ", start + 1, step + 1)
        start = back + 1 if back > start else step
    return spans, 0


def serve(row, fills, seed, vocabulary, window=None):
    """The texts one example is served as: [{"text", "chunks", "spans", "unscanned"}].

    window is (size, stride) for the offline windowed variant; None cuts as the runtime does.
    """
    out = []
    for index in range(fills):
        key = f"{seed}/{row['id']}/{index}".encode()
        edits = []
        text = fill(row["text"], random.Random(hashlib.sha256(key).digest()), vocabulary, edits)
        served, redacted = redact(text)
        spans = None  # by facet, in the served text; None when redaction moved the text
        if not redacted:
            spans = {
                name: carry(note["span"], edits)
                for name, note in row["facets"].items()
                if note["present"]
            }
        chunks, unscanned = window_spans(served, *window) if window else chunk_spans(served)
        item = {"text": served, "chunks": chunks, "spans": spans, "unscanned": unscanned}
        # For a caller that carries other spans of the template: the replacements made, and
        # whether redaction then moved the text, in which case nothing can be carried.
        out.append(item | {"edits": edits, "redacted": bool(redacted)})
    return out


def observe(rows, probes, similarities, fills=1, seed=0, vocabulary="dev", window=None):
    """Per example and facet, what the probes did: averaged over the fills.

    similarities(texts) gives a row per text and a column per probe, or that and the rows
    of the texts the model was given only part of (common.probe_scores). Returns one record
    per (example, facet) and the coverage of the serving path. A piece embedded in part is
    scored by that part; the example it belongs to says so ("in_part") and coverage counts
    the pieces, so that no rate is read as if every piece had been scored whole.
    """
    served = [serve(row, fills, seed, vocabulary, window) for row in rows]
    texts, owner = [], []
    for number, variants in enumerate(served):
        for variant, item in enumerate(variants):
            for start, end in item["chunks"]:
                texts.append(item["text"][start:end])
                owner.append((number, variant))
    found = similarities(texts)
    found, cut = found if isinstance(found, tuple) else (found, ())
    cut = set(cut)
    scores = np.asarray(found, dtype=np.float64).reshape(len(texts), len(probes))
    readable = np.array([len(text.strip()) >= MIN_RANKED_CHARS for text in texts], dtype=bool)
    at = {}
    for position, key in enumerate(owner):
        at.setdefault(key, []).append(position)
    records = []
    for number, (row, variants) in enumerate(zip(rows, served, strict=True)):
        for column, probe in enumerate(probes):
            note = row["facets"][probe["id"]]
            record = {"block": row["block"], "probe": probe["id"], "present": note["present"]}
            seen = {"tagged": [], "best": [], "on_span": [], "top_on_span": [], "split": []}
            seen |= {"unranked": [], "in_part": []}
            for variant, item in enumerate(variants):
                # Only chunks the runtime would rank count, for the tag and for every ranking
                # measure alike. An example with none is untagged and ranks below all others.
                where = [p for p in at.get((number, variant), []) if readable[p]]
                seen["in_part"].append(any(p in cut for p in where))
                mine = scores[where, column]
                hits = mine >= probe["threshold"]
                seen["tagged"].append(bool(hits.any()))
                seen["best"].append(float(mine.max()) if where else NO_SCORE)
                seen["unranked"].append(not where)
                if not note["present"] or item["spans"] is None:
                    continue
                start, end = item["spans"][probe["id"]]
                ranked = [item["chunks"][p - at[(number, variant)][0]] for p in where]
                over = np.array([a < end and start < b for a, b in ranked], dtype=bool)
                seen["split"].append(not any(a <= start and end <= b for a, b in item["chunks"]))
                seen["on_span"].append(bool((hits & over).any()))
                seen["top_on_span"].append(bool(where) and bool(over[int(mine.argmax())]))
            # Per fill; None where nothing could be ranked. A missing score is never a number.
            record["bests"] = seen.pop("best")
            scored = [best for best in record["bests"] if best is not None]
            record["best"] = float(np.mean(scored)) if scored else None
            record |= {name: float(np.mean(values)) for name, values in seen.items() if values}
            record["unmapped"] = bool(note["present"]) and "split" not in record
            records.append(record)
    coverage = {"examples": len(rows), "fills": fills, "chunks": len(texts)}
    coverage["unscanned_chars"] = sum(item["unscanned"] for variants in served for item in variants)
    coverage["unreadable_chunks"] = int((~readable).sum())
    coverage["chunks_in_part"] = len(cut)  # embedded as far as the model's windows reach
    coverage["redacted"] = sum(item["spans"] is None for variants in served for item in variants)
    return records, coverage


def _rate(values):
    return round(float(np.mean(values)), 3) if len(values) else None


def measure(records):
    """One facet's numbers over a set of records. Rates average each example's share of fills."""
    yes = [r for r in records if r["present"]]
    no = [r for r in records if not r["present"]]
    hit, false = sum(r["tagged"] for r in yes), sum(r["tagged"] for r in no)
    mapped = [r for r in yes if not r["unmapped"]]
    split = [r for r in mapped if r["split"] > 0]
    row = {"present": len(yes), "absent": len(no)}
    row |= {"recall": _rate([r["tagged"] for r in yes]), "fpr": _rate([r["tagged"] for r in no])}
    row["precision"] = round(hit / (hit + false), 3) if hit + false else None
    # An example with no score ranks below every scored one, whatever the scores' range.
    scored = [r["best"] for r in records if r["best"] is not None]
    floor = (min(scored) if scored else 0.0) - 1.0
    best = [floor if r["best"] is None else r["best"] for r in records]
    score = auc(best, [r["present"] for r in records])
    row["auc"] = None if score is None else round(score, 3)
    row["evidence_tagged"] = _rate([r["on_span"] for r in mapped])
    row["evidence_top_chunk"] = _rate([r["top_on_span"] for r in mapped])
    row |= {"split_evidence": len(split), "split_recall": _rate([r["tagged"] for r in split])}
    row["unmapped"] = len(yes) - len(mapped)
    row["unranked"] = sum(r["unranked"] > 0 for r in records)  # had a fill with no rankable chunk
    # Examples with a piece the model was given only part of, among those present and absent.
    row["in_part"] = [sum(r.get("in_part", 0) > 0 for r in group) for group in (yes, no)]
    return row


def report(records, probes, resamples=1000, seed=0):
    """Rows per facet: each block, pooled, the mean over blocks, and without the largest block."""
    rng = random.Random(seed)
    for probe in probes:
        mine = [r for r in records if r["probe"] == probe["id"]]
        blocks = Counter(r["block"] for r in mine)
        by = {block: [r for r in mine if r["block"] == block] for block in blocks}
        head = {"probe": probe["id"], "threshold": probe["threshold"]}
        for number, block in enumerate(sorted(by, key=lambda b: (-blocks[b], b)), 1):
            # A block is named by its rank in size: block names stay with the manifest.
            yield (
                head | {"scope": f"block {number}", "examples": blocks[block]} | measure(by[block])
            )
        pooled = measure(mine)
        if len(by) > 1:
            for key in ("recall", "fpr"):
                drawn = []
                for _ in range(resamples):
                    sample = [r for b in rng.choices(sorted(by), k=len(by)) for r in by[b]]
                    if (value := measure(sample)[key]) is not None:
                        drawn.append(value)
                if drawn:
                    low, high = np.percentile(drawn, [2.5, 97.5])
                    pooled[f"{key}_interval"] = [round(float(low), 3), round(float(high), 3)]
        yield head | {"scope": "pooled", "blocks": len(by), "examples": len(mine)} | pooled
        if len(by) > 1:
            each = [measure(group) for group in by.values()]
            mean = {
                key: _rate([row[key] for row in each if row[key] is not None])
                for key in ("recall", "fpr", "precision", "evidence_tagged", "evidence_top_chunk")
            }
            yield head | {"scope": "mean over blocks", "blocks": len(by)} | mean
            largest = max(blocks, key=lambda b: (blocks[b], b))
            rest = [r for r in mine if r["block"] != largest]
            yield (
                head | {"scope": "without the largest block", "examples": len(rest)} | measure(rest)
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", help="evaluation examples (JSONL) that passed check_eval.py")
    parser.add_argument("--use", choices=tuple(VOCABULARY), required=True)
    parser.add_argument("--model", default="google/embeddinggemma-2")
    parser.add_argument("--probes", help="probes.jsonl; the model's own or the built-in if omitted")
    parser.add_argument(
        "--variant",
        help="a frozen variant written by fit_variant.py: its probes, thresholds and windows",
    )
    parser.add_argument("--device", default="auto", help="auto, cuda, mps or cpu")
    parser.add_argument("--fills", type=int, default=3, help="entity fills per example")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--code", help="the source commit, when this is not a Git checkout")
    args = parser.parse_args(argv)

    source = source_identity(args.use, args.code)  # before any model is loaded or text scored
    window, variant, variant_sha = None, None, None
    if args.variant:
        raw, variant_sha = read_once(args.variant)
        variant = json.loads(raw.decode("utf-8"))
        name, probes = variant["name"], variant["probes"]
        try:
            window = check_window(tuple(variant["window"]) if variant.get("window") else None)
        except ValueError as error:
            raise SystemExit(f"{args.variant}: {error}") from None
    else:
        name, probes = choose_probes(args.model, args.probes)
    probes = [probe for probe in probes if calibrated(probe)]
    data, data_sha = read_once(args.file)
    rows = rows_of(data)
    if any(seeds_of(row) is None or set(row["facets"]) < {p["id"] for p in probes} for row in rows):
        raise SystemExit("run train/check_eval.py on this file first")
    model = load_model(args.model, args.device)
    records, coverage = observe(
        rows,
        probes,
        lambda texts: probe_scores(model, probes, texts),
        args.fills,
        args.seed,
        VOCABULARY[args.use],
        window,
    )
    run = {"file": args.file, "use": args.use, "fills": args.fills, "seed": args.seed}
    run |= {"file_sha256": data_sha, "probes": name}
    run["probes_sha256"] = hashlib.sha256(json.dumps(probes, sort_keys=True).encode()).hexdigest()
    run |= model_identity(args.model, model, args.device)
    run |= source
    if variant is not None:  # an offline experiment unless it is the runtime's own serving
        run |= {"variant_sha256": variant_sha, "window": variant.get("window")}
        run["serving"] = "runtime" if variant.get("runtime") else "offline experiment"
    print(json.dumps({"run": run, "coverage": coverage}))
    for row in report(records, probes, seed=args.seed):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
