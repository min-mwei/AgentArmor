"""Compare candidate variants of the probes on development data, before any is frozen.

    python train/study_variants.py --dev train/data/eval/pilot-001.jsonl \\
        train/data/eval/dev-002.jsonl --seeds train/data/seeds/all.jsonl \\
        --blocks train/data/eval/blocks.jsonl --allow train/data/eval/allow-dev-002.jsonl

Development files only: every row passes check_eval.eligible first, like fit_variant.py. For
each variant (the runtime's consecutive chunks or overlapping windows of a size and stride;
no contrast or negative queries at a weight) it prints, per facet:

- auc: ranking of examples by their best piece, over all rows. No threshold involved.
- held-out recall and fpr: the threshold is fitted for the target recall on all source blocks
  but one and applied to that one, for each block in turn; the figures pool those held-out
  rows. This is what a threshold fitted on some sources does on another.
- evidence_top: share of positive examples whose best piece overlaps the annotated span.
- split: positive examples whose span lies in no single piece.

Document embeddings are computed once per distinct text and reused across variants. A piece
longer than the model's window is embedded in windows and takes its best one, as the runtime
scores it. Nothing is written but the report; fit_variant.py writes the variants that are chosen.
"""

import argparse
import json

import numpy as np
from check_eval import eligible
from common import QUERY_PROMPT, auc, encode, load_model, served
from fit_variant import choose_threshold
from score_eval import (
    VOCABULARY,
    check_window,
    choose_probes,
    code_version,
    model_identity,
    observe,
    read_once,
    rows_of,
)

from summarizer.embedder import best_window, unit


def held_out(records, probe, recall):
    """Pooled (recall, fpr, blocks) with each block scored at a threshold fitted on the others."""
    mine = [r for r in records if r["probe"] == probe]
    hit = total = false = negatives = 0
    blocks = sorted({r["block"] for r in mine})
    for block in blocks:
        rest = [r for r in mine if r["block"] != block]
        yes = [best for r in rest if r["present"] for best in r["bests"]]
        ranked = [best for best in yes if best is not None]
        if len(ranked) < 2:
            continue
        threshold, _ = choose_threshold(ranked, len(yes), recall)
        for r in (r for r in mine if r["block"] == block):
            tagged = sum(best is not None and best >= threshold for best in r["bests"])
            if r["present"]:
                hit, total = hit + tagged, total + len(r["bests"])
            else:
                false, negatives = false + tagged, negatives + len(r["bests"])
    rate = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
    return rate(hit, total), rate(false, negatives), len(blocks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dev", nargs="+", required=True)
    parser.add_argument("--seeds", action="append", required=True)
    parser.add_argument("--blocks", required=True)
    parser.add_argument("--allow")
    parser.add_argument("--benchset")
    parser.add_argument("--model", default="google/embeddinggemma-2")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--negatives", help='JSONL of {"id", "negatives"} per probe')
    parser.add_argument("--weights", type=float, nargs="*", default=[0.25, 0.5, 1.0])
    parser.add_argument("--windows", nargs="*", default=["480/160", "640/320", "320/160"])
    parser.add_argument("--recall", type=float, default=0.9)
    parser.add_argument("--fills", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    _, probes = choose_probes(args.model)
    files, rows = {}, []
    for path in args.dev:
        data, files[path] = read_once(path)
        rows += rows_of(data)
    gate = eligible(rows, probes, "development", args.seeds, args.blocks, args.allow, args.benchset)
    wrong = [problem for problem in gate["problems"] if not problem.get("review")]
    if wrong:
        raise SystemExit(f"{len(wrong)} problems: not development data that may be studied")
    windows = [None] + [check_window(tuple(int(n) for n in w.split("/"))) for w in args.windows]
    against, negatives_sha = {}, None
    if args.negatives:
        data, negatives_sha = read_once(args.negatives)
        against = {row["id"]: row["negatives"] for row in rows_of(data)}

    model = load_model(args.model, args.device)
    positive = unit(
        np.stack([encode(model, p["queries"], QUERY_PROMPT).mean(axis=0) for p in probes])
    )
    negative = None
    if against:
        negative = unit(
            np.stack([encode(model, against[p["id"]], QUERY_PROMPT).mean(axis=0) for p in probes])
        )
    cache = {}

    def documents(texts):
        """For each text its vector, its windows' when it was embedded as several, and
        whether some of it is in no window."""
        new = [text for text in dict.fromkeys(texts) if text not in cache]
        if new:
            embedder = served(model)
            vectors = embedder.documents(new)
            for row, text in enumerate(new):
                cache[text] = (vectors[row], embedder.parts.get(row), row in embedder.cut)
        return [cache[text] for text in texts]

    run = {"files": files, "examples": len(rows), "fills": args.fills, "seed": args.seed}
    run |= {"recall": args.recall, "negatives_sha256": negatives_sha, "code": code_version()}
    run |= {"seed_corpus_sha256": gate["corpus"], "allowances_sha256": gate["allowances_sha256"]}
    print(json.dumps({"run": run | model_identity(args.model, model, args.device)}))
    for window in windows:
        for weight in [0.0] + (args.weights if negative is not None else []):

            def similar(texts, weight=weight):
                def score(vectors):
                    scores = vectors @ positive.T
                    return scores - weight * (vectors @ negative.T) if weight else scores

                found = documents(texts)
                scores = [best_window(score, vector, parts) for vector, parts, _ in found]
                cut = [row for row, (_, _, in_part) in enumerate(found) if in_part]
                return (np.stack(scores) if scores else np.zeros((0, len(probes)))), cut

            records, coverage = observe(
                rows, probes, similar, args.fills, args.seed, VOCABULARY["development"], window
            )
            for probe in probes:
                mine = [r for r in records if r["probe"] == probe["id"]]
                yes = [r for r in mine if r["present"]]
                scored = [r["best"] for r in mine if r["best"] is not None]
                floor = (min(scored) if scored else 0.0) - 1.0
                best = [floor if r["best"] is None else r["best"] for r in mine]
                mapped = [r for r in yes if not r["unmapped"]]
                recall, fpr, blocks = held_out(records, probe["id"], args.recall)
                row = {"window": list(window) if window else None, "contrast": weight}
                row |= {"probe": probe["id"], "present": len(yes), "absent": len(mine) - len(yes)}
                row["auc"] = round(auc(best, [r["present"] for r in mine]), 3)
                row |= {"held_out_recall": recall, "held_out_fpr": fpr, "blocks": blocks}
                row["evidence_top"] = round(float(np.mean([r["top_on_span"] for r in mapped])), 3)
                row["split"] = sum(r["split"] > 0 for r in mapped)
                row["pieces"] = coverage["chunks"]
                row["pieces_in_part"] = coverage["chunks_in_part"]
                print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
