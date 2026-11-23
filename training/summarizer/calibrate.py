"""Calibrate a model's probes on labelled pairs, favoring recall.

    python training/summarizer/calibrate.py --model training/summarizer/runs/NAME/model --data training/summarizer/data/built/facets.dev.jsonl

For each probe this fits scale and bias so that sigmoid(scale * similarity + bias) is the chance
the probe's label applies, and picks the similarity threshold that still tags the target share of
the positives (90% by default). The summarizer tags content at or above the threshold and reports
the probability as the tag's confidence. Results are written as probes.jsonl beside the model (or
to --out), with the recall and false-positive rate measured on the calibration pairs recorded on
each probe. Calibrate on pairs the model was not trained on.
"""

import argparse
from pathlib import Path

import numpy as np

from common import (
    PartialInput,
    auc,
    inference,
    load_model,
    load_probes,
    pick_device,
    probe_similarities,
    read_jsonl,
    spans,
)
from summarizer.probes import dump


def platt(similarities, labels, steps=200):
    """Scale and bias of a one-feature logistic fit, by Newton's method with a light ridge."""
    x = np.asarray(similarities, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    center, spread = x.mean(), max(x.std(), 1e-6)
    z = (x - center) / spread
    weights = np.zeros(2)
    design = np.stack([z, np.ones_like(z)], axis=1)
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-(design @ weights)))
        gradient = design.T @ (p - y) + 1e-3 * weights
        hessian = (design * (p * (1 - p))[:, None]).T @ design + 1e-3 * np.eye(2)
        step = np.linalg.solve(hessian, gradient)
        weights -= step
        if np.abs(step).max() < 1e-9:
            break
    scale = weights[0] / spread
    return float(scale), float(weights[1] - scale * center)


def calibrate(similarities, labels, recall=0.9):
    """Calibration of one probe: scale, bias, a recall-first threshold and what it costs."""
    similarities, labels = np.asarray(similarities, dtype=np.float64), np.asarray(labels, bool)
    positive, negative = similarities[labels], similarities[~labels]
    if len(positive) < 2 or len(negative) < 2:
        raise ValueError("calibration needs at least two positive and two negative pairs")
    scale, bias = platt(similarities, labels)
    # The highest threshold that still tags the target share of positives.
    threshold = float(np.sort(positive)[int(np.floor((1 - recall) * len(positive)))])
    # Saved to four places, rounded down so the stored threshold never tags fewer positives than
    # the exact one; everything reported below is measured with the stored value.
    threshold = float(np.floor(threshold * 1e4) / 1e4)
    return {
        "scale": round(scale, 4),
        "bias": round(bias, 4),
        "threshold": threshold,
        "calibration": {
            "recall": round(float((positive >= threshold).mean()), 3),
            "fpr": round(float((negative >= threshold).mean()), 3),
            "auc": round(auc(similarities, labels), 3),
            "positives": int(len(positive)),
            "negatives": int(len(negative)),
        },
    }


def identity(model, device, pairs):
    """What a calibration holds for: the device and precision it was measured in, and the data."""
    import hashlib

    text = "\n".join(sorted(f"{pair['probe']}\t{pair['label']}\t{pair['text']}" for pair in pairs))
    # How many of the texts were embedded as one window, as two, and so on: a fit on texts
    # of one window has measured nothing about the best of several, and says so here. And
    # how many were embedded only in part: none in a fit, which calibrate_probes() refuses.
    by_windows, cut = spans(model, sorted({pair["text"] for pair in pairs}))
    told = inference(model) | {"texts_by_windows": by_windows, "texts_in_part": len(cut)}
    return {
        "device": str(device),
        "dtype": str(next(model.parameters()).dtype).removeprefix("torch."),
        "inference": told,
        "pairs_sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:16],
        "groups": len({pair.get("group") for pair in pairs}),
    }


def calibrate_probes(model, probes, pairs, recall=0.9, measured=None):
    """The probes fitted on pairs. A pair whose text the model could be given only part of
    stops the fit (common.PartialInput): its label is of the whole text and its score would
    be of the beginning."""
    texts = sorted({pair["text"] for pair in pairs})
    index = {text: row for row, text in enumerate(texts)}
    similarities = probe_similarities(model, probes, texts)
    out = []
    for column, probe in enumerate(probes):
        mine = [pair for pair in pairs if pair["probe"] == probe["id"]]
        scores = [similarities[index[pair["text"]], column] for pair in mine]
        fitted = calibrate(scores, [pair["label"] > 0 for pair in mine], recall)
        if measured:  # thresholds hold for this precision and device, and say so
            fitted["calibration"] |= measured
        out.append({**probe, **fitted})
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="google/embeddinggemma-2")
    parser.add_argument("--data", required=True, help="facets.<split>.jsonl from build_dataset.py")
    parser.add_argument(
        "--probes", help="probes.jsonl to calibrate; the built-in probes if omitted"
    )
    parser.add_argument("--recall", type=float, default=0.9, help="share of positives to keep")
    parser.add_argument("--device", default="auto")
    parser.add_argument(
        "--out", help="where to write probes.jsonl; beside a local model if omitted"
    )
    args = parser.parse_args(argv)

    _, probes = load_probes(args.probes)
    device = pick_device(args.device)
    model = load_model(args.model, device)
    pairs = read_jsonl(args.data)
    try:
        measured = identity(model, device, pairs)
        result = calibrate_probes(model, probes, pairs, args.recall, measured)
    except PartialInput as error:
        raise SystemExit(f"{args.data}: {error} Nothing is fitted.") from None
    out = Path(args.out) if args.out else Path(args.model) / "probes.jsonl"
    dump(result, out)
    for probe in result:
        print(probe["id"], probe["threshold"], probe["calibration"])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
