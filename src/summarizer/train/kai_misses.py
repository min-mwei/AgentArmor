"""Where a second-stage model's misses sit: by panel, by injected instruction, by genre.

    python train/kai_misses.py --benchset ~/repos/AgentArmor/benchset

Measurement only (the benchset is never trained or calibrated on), and no model is run: it
reads the benchset's cases and the scores saved in benchset/predictions/<system>.jsonl. It
answers three questions about the attacks a system scored at or below its 0.5 cutoff:

- are they near the cutoff or far below it (how many score under 0.01, and where the missed
  attacks sit in log-odds next to the benign cases);
- on the BIPIA panel, which injected instructions they are. A case id ends in the placement
  and an id of the instruction, so this needs no reading of case text;
- on the short-text panel, which genres they are, and which genres the false alarms are.

With --reads it does the same for other trained copies of the model, from the per-case rows
Kai's trainer saves for each read (one folder per model, local to the Kai repository):

    python train/kai_misses.py --benchset ~/repos/AgentArmor/benchset \\
        --reads ~/repos/Kai/train/runs/qwen-rc2-recovery99-run1-s{0,1,2}-dev-20261108

Those rows carry the short-text panel's own tags, so each read is also broken down by what the
planted instruction does, how it is phrased and what kind of benign twin it has. Given several
reads, a last line scores the mean of their log-odds: what averaging the copies would do.

It prints one JSON object per line. Only ids, counts and scores are printed, never case text.
"""

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

from common import read_jsonl
from evaluate import PANELS

CONFIDENT = 0.01  # a missed attack scored below this is a confident miss, not a near miss
GENRE = re.compile(r"/dev-([a-z_]+)-[A-Z]-r\d+[ab]$")
QUESTION = "is_xpia_attempt"
READ_FILES = {  # a panel's file in one of Kai's saved read folders
    "xpia-hw1": "hw1-dev-xpia-rc2-catalog.jsonl",
    "xpia-armor": "armor-v2-xpia-rc2-catalog.jsonl",
    "xpia-shorttext": "shorttext-dev.jsonl",
    "xpia-bipia": "bipia-dev-primary-only.jsonl",
}
TAGS = ("effect", "variant", "root_type")  # the short-text panel's tags worth splitting by


def log_odds(score):
    score = min(max(score, 1e-300), 1 - 1e-16)
    return math.log(score) - math.log1p(-score)


def quartiles(values):
    """Lowest, lower quartile, median, upper quartile and highest, by nearest rank."""
    ordered = sorted(values)
    if not ordered:
        return None
    last = len(ordered) - 1
    return [round(ordered[round(share * last)], 1) for share in (0, 0.25, 0.5, 0.75, 1)]


def within_budget(attacks, benign, share):
    """Attacks scoring above all but floor(share x benign) of the benign cases.

    The cutoff is fitted on the very cases counted, so this says how well the scores rank, not
    what a deployed cutoff would catch.
    """
    allowed = int(share * len(benign))
    ordered = sorted(benign, reverse=True)
    cutoff = ordered[allowed] if allowed < len(ordered) else -1.0
    return sum(score > cutoff for score in attacks)


def panel_row(panel, cases, saved):
    attacks = [saved[case["id"]] for case in cases if case["label"]]
    benign = [saved[case["id"]] for case in cases if not case["label"]]
    missed = [score for score in attacks if not score > 0.5]
    return {
        "panel": panel,
        "attacks": len(attacks),
        "missed": len(missed),
        "missed_below_0.01": sum(score < CONFIDENT for score in missed),
        "benign": len(benign),
        "benign_flagged": sum(score > 0.5 for score in benign),
        "benign_above_0.99": sum(score > 1 - CONFIDENT for score in benign),
        "ranked_above_all_but_1pct_of_benign": within_budget(attacks, benign, 0.01),
        "ranked_above_all_but_5pct_of_benign": within_budget(attacks, benign, 0.05),
        "log_odds_quartiles": {
            "benign": quartiles([log_odds(score) for score in benign]),
            "missed_attacks": quartiles([log_odds(score) for score in missed]),
        },
    }


def by_instruction(cases, saved):
    """BIPIA attacks by the instruction injected: {id: {"place", "attacks", "missed"}}."""
    found = {}
    for case in cases:
        parts = case["id"].split("/")
        if not case["label"] or len(parts) < 4:
            continue
        place, instruction = parts[-2], parts[-1]
        row = found.setdefault(instruction, {"place": Counter(), "attacks": 0, "missed": 0})
        row["place"][place] += 1
        row["attacks"] += 1
        row["missed"] += not saved[case["id"]] > 0.5
    ranked = sorted(found.items(), key=lambda item: (-item[1]["missed"], item[0]))
    return {name: row | {"place": dict(row["place"])} for name, row in ranked}


def by_genre(cases, saved):
    """Short-text cases by genre: {genre: {"attacks", "missed", "benign", "benign_flagged"}}."""
    found = {}
    for case in cases:
        match = GENRE.search(case["id"])
        blank = {"attacks": 0, "missed": 0, "benign": 0, "benign_flagged": 0}
        row = found.setdefault(match.group(1) if match else "other", blank)
        score = saved[case["id"]]
        if case["label"]:
            row["attacks"] += 1
            row["missed"] += not score > 0.5
        else:
            row["benign"] += 1
            row["benign_flagged"] += score > 0.5
    return dict(sorted(found.items(), key=lambda item: -item[1]["missed"]))


def by_tag(cases, saved, tags, name):
    """Cases by one of the panel's tags, as by_genre counts them: {value: {...}}."""
    found = {}
    for case in cases:
        value = tags.get(case["id"], {}).get(name)
        if value is None:
            continue
        blank = {"attacks": 0, "missed": 0, "benign": 0, "benign_flagged": 0}
        row = found.setdefault(value, blank)
        score = saved[case["id"]]
        if case["label"]:
            row["attacks"] += 1
            row["missed"] += not score > 0.5
        else:
            row["benign"] += 1
            row["benign_flagged"] += score > 0.5
    return dict(sorted(found.items(), key=lambda item: -item[1]["missed"]))


def read_folder(folder):
    """One model's saved read: ({case id: score}, {case id: {tag: value}})."""
    saved, tags = {}, {}
    for name in READ_FILES.values():
        for row in read_jsonl(Path(folder).expanduser() / name):
            if row["question"] != QUESTION:
                continue
            saved[row["record"]] = row["probabilities"][row["positive_option"]]
            listed = row.get("meta", {}).get("tags") or []
            listed = json.loads(listed) if isinstance(listed, str) else listed
            tags[row["record"]] = dict(item.split(":", 1) for item in listed if ":" in item)
    return saved, tags


def averaged(reads):
    """The mean of several reads' log-odds per case, back as a score."""
    out = {}
    for case in reads[0]:
        mean = sum(log_odds(saved[case]) for saved in reads) / len(reads)
        out[case] = 1.0 / (1.0 + math.exp(-mean))
    return out


def describe(name, panels, saved, tags):
    """Every panel row for one system, then its pooled count."""
    missing = [case["id"] for cases in panels.values() for case in cases if case["id"] not in saved]
    if missing:  # never score the cases two systems happen to share
        raise SystemExit(f"{name}: no score for {len(missing)} cases, such as {missing[0]}")
    caught = flagged = 0
    for panel, cases in panels.items():
        row = {"system": name} | panel_row(panel, cases, saved)
        if panel == "xpia-bipia":
            row["by_instruction"] = by_instruction(cases, saved)
        if panel == "xpia-shorttext":
            row["by_genre"] = by_genre(cases, saved)
            row |= {f"by_{tag}": by_tag(cases, saved, tags, tag) for tag in TAGS if tags}
        caught += row["attacks"] - row["missed"]
        flagged += row["benign_flagged"]
        print(json.dumps(row))
    print(json.dumps({"system": name, "attacks_caught": caught, "benign_flagged": flagged}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--benchset", required=True, help="AgentArmor benchset directory")
    parser.add_argument("--system", default="kai-4b", help="whose saved scores to use")
    parser.add_argument("--reads", nargs="*", default=[], help="saved read folders of Kai models")
    args = parser.parse_args(argv)

    root = Path(args.benchset).expanduser()
    panels = {panel: read_jsonl(root / "cases" / f"{panel}.jsonl") for panel in PANELS}
    if not args.reads:
        rows = read_jsonl(root / "predictions" / f"{args.system}.jsonl")
        describe(args.system, panels, {row["id"]: row["score"] for row in rows}, {})
        return 0
    reads = []
    for folder in args.reads:
        saved, tags = read_folder(folder)
        describe(Path(folder).name, panels, saved, tags)
        reads.append(saved)
    if len(reads) > 1:
        describe(f"mean log-odds of {len(reads)} reads", panels, averaged(reads), {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
