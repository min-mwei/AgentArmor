"""Measure how well a model's probes, and the summarizer's wording rules, pick out steering text.

    python train/evaluate.py --model google/embeddinggemma-2 --data train/data/built
    python train/evaluate.py --model train/runs/NAME/model --benchset ~/repos/AgentArmor/benchset

--data scores the probes on each facets.<split>.jsonl: AUC, and recall and false-positive rate at
the probe's calibrated threshold. Compare train with dev and heldout: a model that learned the
pattern holds up on groups, vocabularies and carrier families it never saw; one that memorized
does not.

--benchset reads AgentArmor's public prompt-injection panels for measurement only (never train or
calibrate on them). Each case counts as tagged if any chunk of its text earns an indicator, from
wording rules, from calibrated probes, or from either. Recall is the share of attacks tagged;
tag rate on benign cases is the price paid for it.

Results are printed as JSON Lines, one measurement per line.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from common import auc, load_model, load_probes, probe_scores, read_jsonl

from summarizer.events import chunk_spans
from summarizer.facts import hidden_text, path_mentions, text_flags
from summarizer.probes import calibrated
from summarizer.summarize import MIN_RANKED_CHARS

PANELS = ("xpia-hw1", "xpia-armor", "xpia-shorttext", "xpia-bipia")


def probe_report(model, probes, pairs, split):
    texts = sorted({pair["text"] for pair in pairs})
    index = {text: row for row, text in enumerate(texts)}
    similarities, cut = probe_scores(model, probes, texts)
    cut = set(cut)
    for column, probe in enumerate(probes):
        mine = [pair for pair in pairs if pair["probe"] == probe["id"]]
        scores = np.array([similarities[index[pair["text"]], column] for pair in mine])
        labels = np.array([pair["label"] > 0 for pair in mine])
        row = {"split": split, "probe": probe["id"], "pairs": len(mine), "auc": auc(scores, labels)}
        # Pairs whose text the model was given only part of: counted in the rates below
        # by the score of that part, and said here so that the rates can be read.
        row["pairs_in_part"] = sum(index[pair["text"]] in cut for pair in mine)
        if calibrated(probe) and labels.any() and not labels.all():
            tagged = scores >= probe["threshold"]
            row["recall"] = round(float(tagged[labels].mean()), 3)
            row["fpr"] = round(float(tagged[~labels].mean()), 3)
        row["auc"] = None if row["auc"] is None else round(row["auc"], 3)
        yield row


def benchset_report(model, probes, benchset, limit):
    usable = [probe for probe in probes if calibrated(probe)]
    for panel in PANELS:
        cases = read_jsonl(Path(benchset).expanduser() / "cases" / f"{panel}.jsonl")[:limit]
        labels = np.array([bool(case["label"]) for case in cases])
        chunks, owner = [], []
        for number, case in enumerate(cases):
            text = case["request"]["state"]
            text = text if isinstance(text, str) else json.dumps(text)
            for start, end in chunk_spans(text)[0]:
                chunks.append(text[start:end])
                owner.append(number)
        owner = np.array(owner)
        worded = np.array(
            [bool(text_flags(c) or path_mentions(c) or hidden_text(c)) for c in chunks]
        )
        probed, cut = np.zeros(len(chunks), dtype=bool), []
        if usable:
            similarities, cut = probe_scores(model, usable, chunks)
            thresholds = np.array([probe["threshold"] for probe in usable])
            readable = np.array([len(chunk.strip()) >= MIN_RANKED_CHARS for chunk in chunks])
            probed = (similarities >= thresholds).any(axis=1) & readable  # as the runtime tags
        for source, hits in (("wording", worded), ("probes", probed), ("either", worded | probed)):
            tagged = np.zeros(len(cases), dtype=bool)
            np.logical_or.at(tagged, owner, hits)
            yield {
                "panel": panel,
                "source": source,
                "cases": len(cases),
                # Cases with a piece the model was given only part of, for the probes' rows.
                "cases_in_part": len({int(owner[row]) for row in cut}),
                "attack_recall": round(float(tagged[labels].mean()), 3),
                "benign_tag_rate": round(float(tagged[~labels].mean()), 3),
            }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="google/embeddinggemma-2")
    parser.add_argument("--probes", help="probes.jsonl; the model's own or the built-in if omitted")
    parser.add_argument("--data", help="directory of facets.<split>.jsonl from build_dataset.py")
    parser.add_argument("--benchset", help="AgentArmor benchset directory (measurement only)")
    parser.add_argument("--limit", type=int, default=400, help="cases read per benchset panel")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)

    probes = args.probes
    if probes is None and (Path(args.model) / "probes.jsonl").is_file():
        probes = args.model
    _, probes = load_probes(probes)
    model = load_model(args.model, args.device)
    rows = []
    if args.data:
        for path in sorted(Path(args.data).glob("facets.*.jsonl")):
            rows += probe_report(model, probes, read_jsonl(path), path.name.split(".")[1])
    if args.benchset:
        rows += benchset_report(model, probes, args.benchset, args.limit)
    for row in rows:
        print(json.dumps(row))
    return rows


if __name__ == "__main__":
    main()
