"""Fit and write one variant of the probes for the facet evaluation, on development data only.

    python train/fit_variant.py --name windows --dev train/data/eval/pilot-001.jsonl \\
        train/data/eval/dev-002.jsonl --seeds train/data/seeds/all.jsonl \\
        --blocks train/data/eval/blocks.jsonl --window 480 160 \\
        --out train/data/eval/variants/windows.json

A variant is what score_eval.py --variant runs: the probes with their queries, optional
negative queries scored by contrast, an optional window (size, stride) and a threshold per
probe. The threshold is refitted here for a target recall on the development examples, served
exactly as score_eval.py serves them (same fills, seed and vocabulary).

- Every input file passes check_eval.eligible for use "development" before any model is
  loaded: block and seed bindings, labels, and overlap with the seed texts unless a reviewed
  allowance (--allow) covers it. A row from a test or unknown block, or an unreviewed overlap,
  stops the run and nothing is written.
- The recall target is over **all** served positive fills. A fill with no piece long enough
  to rank is a miss, as it is for the scorer, not a fill left out. When too few positives can
  be ranked to reach the target, the fit says so ("unattainable") and takes the lowest
  threshold that tags every rankable one.
- Each input is read once; the bytes that are hashed are the bytes that are fitted on. The
  artifact records them with the model, device, precision and source commit.

--as-is keeps the chosen probes' own thresholds and fits nothing: the runtime as it stands for
that model (the named probe file, else the model's own, else the built-in set), written in the
same form so that every variant is scored by the same code.
"""

import argparse
import json
import math
from pathlib import Path

from calibrate import calibrate
from check_eval import eligible
from common import PartialInput, load_model, probe_similarities
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


def choose_threshold(positives, total, recall):
    """(threshold, attainable) so that at least recall of total positive fills score at or above.

    positives are the scores of the fills that could be ranked; total counts every positive
    fill, ranked or not. The threshold is rounded down to four places so it never tags fewer.
    """
    ranked = sorted(positives, reverse=True)
    needed = math.ceil(recall * total - 1e-9)
    attainable = 0 < needed <= len(ranked)
    chosen = ranked[needed - 1] if attainable else ranked[-1]
    return math.floor(chosen * 1e4) / 1e4, attainable


def fit(rows, probes, similarities, fills, seed, recall, window=None):
    """The probes with threshold, scale and bias refitted on rows, and what the fit measured."""
    records, _ = observe(rows, probes, similarities, fills, seed, VOCABULARY["development"], window)
    fitted = []
    for probe in probes:
        mine = [r for r in records if r["probe"] == probe["id"]]
        yes = [best for r in mine if r["present"] for best in r["bests"]]
        no = [best for r in mine if not r["present"] for best in r["bests"]]
        ranked_yes = [best for best in yes if best is not None]
        ranked_no = [best for best in no if best is not None]
        if len(ranked_yes) < 2 or len(ranked_no) < 2:
            raise ValueError(f"{probe['id']}: fitting needs two ranked positives and negatives")
        shape = calibrate(
            ranked_yes + ranked_no, [True] * len(ranked_yes) + [False] * len(ranked_no), recall
        )
        threshold, attainable = choose_threshold(ranked_yes, len(yes), recall)
        measured = {
            "target_recall": recall,
            "attainable": attainable,
            # Over every served fill: an unrankable one is never tagged.
            "recall": round(sum(best >= threshold for best in ranked_yes) / len(yes), 3),
            "fpr": round(sum(best >= threshold for best in ranked_no) / len(no), 3) if no else None,
            "positive_fills": len(yes),
            "negative_fills": len(no),
            "unrankable_positive_fills": len(yes) - len(ranked_yes),
            "unrankable_negative_fills": len(no) - len(ranked_no),
            "examples": len(mine),
            "rankable_only": shape["calibration"],
        }
        fitted.append(
            probe
            | {"scale": shape["scale"], "bias": shape["bias"]}
            | {"threshold": threshold, "calibration": measured}
        )
    return fitted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", required=True)
    parser.add_argument("--dev", nargs="+", required=True, help="development files (JSONL)")
    parser.add_argument("--seeds", action="append", required=True, help="seed files")
    parser.add_argument("--blocks", required=True, help="the block assignment (JSONL)")
    parser.add_argument("--allow", help="reviewed allowances for seed overlap (JSONL)")
    parser.add_argument("--benchset", help="AgentArmor benchset: nothing may repeat it")
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default="google/embeddinggemma-2")
    parser.add_argument("--probes", help="probes.jsonl; the model's own or the built-in if omitted")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--negatives", help='JSONL of {"id", "negatives"} per probe')
    parser.add_argument("--contrast", type=float, default=1.0, help="weight of the negatives")
    parser.add_argument("--window", type=int, nargs=2, metavar=("SIZE", "STRIDE"))
    parser.add_argument("--recall", type=float, default=0.9)
    parser.add_argument("--fills", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--as-is", action="store_true", help="keep the probes' own thresholds")
    args = parser.parse_args(argv)

    try:
        window = check_window(tuple(args.window) if args.window else None)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    probe_set, probes = choose_probes(args.model, args.probes)
    files, rows = {}, []
    for path in args.dev:
        data, files[path] = read_once(path)
        rows += rows_of(data)
    # The same check, with the same seed texts and allowances, that check_eval.py applies.
    gate = eligible(rows, probes, "development", args.seeds, args.blocks, args.allow, args.benchset)
    wrong = [problem for problem in gate["problems"] if not problem.get("review")]
    if wrong:
        for problem in wrong[:10]:
            print(json.dumps(problem))
        raise SystemExit(f"{len(wrong)} problems: not development data that may be fitted on")

    negatives_sha = None
    if args.negatives:
        data, negatives_sha = read_once(args.negatives)
        against = {row["id"]: row["negatives"] for row in rows_of(data)}
        probes = [p | {"negatives": against[p["id"]], "contrast": args.contrast} for p in probes]
    variant = {"name": args.name, "window": list(window) if window else None, "probes": probes}
    variant["probe_set"] = probe_set
    variant["runtime"] = bool(args.as_is and not args.negatives and not window)
    if not args.as_is:
        model = load_model(args.model, args.device)

        def similar(texts):
            return probe_similarities(model, probes, texts)

        try:
            variant["probes"] = fit(
                rows, probes, similar, args.fills, args.seed, args.recall, window
            )
        except PartialInput as error:  # a fit on a score of half a piece is no fit
            raise SystemExit(f"{error} No variant is written.") from None
        variant["fitted_on"] = {"files": files, "examples": len(rows), "fills": args.fills}
        variant["fitted_on"] |= {"seed": args.seed, "recall": args.recall}
        variant["fitted_on"] |= {"negatives_sha256": negatives_sha, "code": code_version()}
        variant["fitted_on"]["seed_corpus_sha256"] = gate["corpus"]
        variant["fitted_on"]["allowances_sha256"] = gate["allowances_sha256"]
        variant["fitted_on"] |= model_identity(args.model, model, args.device)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(variant, indent=1, ensure_ascii=False) + "\n")
    for probe in variant["probes"]:
        shown = {"probe": probe["id"], "threshold": probe["threshold"]}
        print(json.dumps(shown | probe.get("calibration", {})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
