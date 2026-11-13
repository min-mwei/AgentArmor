"""Check a batch of synthetic examples before it is used for training.

    python train/check_data.py train/data/synthetic/batch-001.jsonl \\
        --seeds train/data/seeds/sessions.jsonl --benchset ~/repos/AgentArmor/benchset

Synthetic examples are written in the image of real seeds. A batch is checked for what would
turn training into memorization, or bring in what must stay out:

- every example is a template in the documented format, with slots the builder can fill and no
  literal host, address or mailbox of its own;
- every group stays in one carrier family and has a benign member (an attack's look-alike, or a
  group of plain text with no attack in it);
- no credential format and no invisible character appears in a text;
- no example repeats a run of words from a seed: seeds are real text, examples must be new;
- no example repeats a run of words from AgentArmor's benchset, which is for measurement only;
- no example repeats a long run of words from an example of another group, no phrase is reused
  across many groups (a batch stamped out of a few clauses teaches those clauses), and no text
  names its own family or group, a probe or its label, or its expected facets, any of which
  would hand the model the answer.

Prints one JSON line per problem and a summary line; exits 1 when there is any problem.
"""

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from build_dataset import fill
from common import load_probes, read_jsonl

from summarizer.facts import hidden_text, observables, visible
from summarizer.redact import redact

REQUIRED = {"id": str, "group": str, "family": str, "facets": list, "text": str}
OPTIONAL = {"seed": str, "note": str}  # the seed an example was written from; a remark for review
OPTIONAL["effects"] = (
    list  # the effects of its wording, by name, so a batch's coverage can be counted
)
# What a piece of steering wording asks for or claims (docs/data-request-003.md, section 3).
# Evaluation files annotate these with spans; a training template only names them.
EFFECTS = (
    "reply_manipulation",
    "rule_or_role_change",
    "authority_impersonation",
    "claimed_approval",
    "bypass_check",
    "concealment",
    "persistence",
    "later_trigger",
    "send_out",
    "delete_or_destroy",
    "disclosure",
    "other",
)
# A template longer than this is refused. It is a bound for the writer and proves nothing about
# tokens: finetune.py counts those, after the prompt is added and the slots are filled, and
# stops on anything the encoder would cut. Long documents belong in evaluation files.
MAX_CHARS = 1000
RUN = 8  # words in a row shared with a seed or the benchset that count as copying
OWN_RUN = 12  # words in a row shared between groups of the same data that count as a near copy
PHRASE, PHRASE_GROUPS = 6, 4  # a run of this many words may appear in at most this many groups
_SLOT = re.compile(r"\{(\w+)\}")
_WORD = re.compile(r"\w+")
# Wording that hands a model the annotation instead of the pattern.
_ANSWER_KEY = re.compile(r"\bexpected[ _-]?(?:facets?|labels?)\b|\bfacets?\s*[=:]", re.I)


def runs(text, size):
    """Every run of size consecutive words in text, lowercased, with slots made alike."""
    words = _WORD.findall(_SLOT.sub(" slot ", text).lower())
    return {tuple(words[index : index + size]) for index in range(len(words) - size + 1)}


def known_slot(name):
    return fill(f"{{{name}}}", random.Random(0), "train") != f"{{{name}}}"


def benchset_texts(path):
    """Every case text of AgentArmor's benchset under path, for the overlap check only."""
    texts = []
    for panel in sorted((Path(path).expanduser() / "cases").glob("*.jsonl")):
        for case in read_jsonl(panel):
            state = case.get("request", {}).get("state", "")
            texts.append(state if isinstance(state, str) else json.dumps(state))
    return texts


def check(rows, probes, seeds=(), benchmark=(), existing=()):
    """Problems in a batch as {"id", "problem", "detail"}; existing are rows already accepted."""
    problems = []

    def report(row, problem, detail=""):
        problems.append({"id": str(row.get("id")), "problem": problem, "detail": str(detail)[:160]})

    facets = {probe["id"] for probe in probes}
    seed_runs = set().union(*(runs(text, RUN) for text in seeds)) if seeds else set()
    bench_runs = set().union(*(runs(text, RUN) for text in benchmark)) if benchmark else set()
    seen_ids = {row["id"] for row in existing}
    owner = {}  # a long run of words -> the group that used it first
    for row in existing:
        for run in runs(row["text"], OWN_RUN):
            owner.setdefault(run, row["group"])
    groups, usable = {}, []
    for row in rows:
        if not isinstance(row, dict):
            report({}, "format", "a line is one JSON object")
            continue
        wrong = [key for key, kind in REQUIRED.items() if not isinstance(row.get(key), kind)]
        wrong += [key for key in row if key not in REQUIRED and key not in OPTIONAL]
        wrong += [
            key for key, kind in OPTIONAL.items() if key in row and not isinstance(row[key], kind)
        ]
        wrong += [  # a list of names holds names: nothing below hashes anything else
            key
            for key in ("facets", "effects")
            if isinstance(row.get(key), list) and not all(isinstance(n, str) for n in row[key])
        ]
        if wrong:
            report(row, "format", f"missing, mistyped or unknown fields: {sorted(set(wrong))}")
            continue
        if row["id"] in seen_ids:
            report(row, "duplicate_id")
        seen_ids.add(row["id"])
        usable.append(row)
        text = row["text"]
        if unknown := sorted(set(row["facets"]) - facets):
            report(row, "unknown_facet", unknown)
        if len(text) > MAX_CHARS:
            said = f"{len(text)} characters; a training template stays within {MAX_CHARS}"
            report(row, "too_long", said)
        named = row.get("effects", [])
        if unknown := sorted({str(name) for name in named} - set(EFFECTS)):
            report(row, "unknown_effect", unknown)
        if unknown := sorted({slot for slot in _SLOT.findall(text) if not known_slot(slot)}):
            report(row, "unknown_slot", unknown)
        if literal := sorted({value for _, _, value in observables(_SLOT.sub(" ", text))}):
            report(row, "literal_entity", f"use a slot instead of {literal}")
        if kinds := redact(text)[1]:
            report(row, "secret_format", sorted(kinds))
        if hidden_text(text) or visible(text) != text:
            report(row, "hidden_characters")
        if runs(text, RUN) & seed_runs:
            report(row, "copies_seed", f"shares {RUN} words in a row with a seed")
        if runs(text, RUN) & bench_runs:
            report(row, "copies_benchset", f"shares {RUN} words in a row with a benchset case")
        group = groups.setdefault(row["group"], {"families": set(), "benign": 0, "row": row})
        group["families"].add(row["family"])
        group["benign"] += not row["facets"]
        for run in runs(text, OWN_RUN):
            if owner.setdefault(run, row["group"]) != row["group"]:
                report(row, "near_copy", f"shares {OWN_RUN} words in a row with group {owner[run]}")
                break
    phrases = {}  # a short run of words -> the groups that use it
    answers = {probe["id"].lower() for probe in probes} | {
        probe["label"].lower() for probe in probes
    }
    for row in usable:
        told = sorted(name for name in answers if name in row["text"].lower())
        if told or _ANSWER_KEY.search(row["text"]):
            what = told or _ANSWER_KEY.search(row["text"])[0]
            report(row, "states_its_labels", f"the text gives away its annotation: {what}")
        tags = {row["family"].lower(), row["group"].lower()}
        if any(tag in row["text"].lower() for tag in tags if "_" in tag or "-" in tag):
            report(row, "names_its_label", "the text contains its own family or group name")
        for run in runs(row["text"], PHRASE):
            phrases.setdefault(run, set()).add(row["group"])
    stamped = {run: used for run, used in phrases.items() if len(used) > PHRASE_GROUPS}
    for row in usable:
        worst = max((run for run in runs(row["text"], PHRASE) if run in stamped),
                    key=lambda run: len(stamped[run]), default=None)  # fmt: skip
        if worst:
            detail = f"{' '.join(worst)!r} appears in {len(stamped[worst])} groups"
            report(row, "repeated_phrasing", detail)
    for name, group in groups.items():
        if len(group["families"]) > 1:
            report(group["row"], "group_families", f"{name} spans {sorted(group['families'])}")
        if not group["benign"]:
            report(group["row"], "group_without_benign", name)
    return problems, usable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("batch", nargs="+", help="synthetic examples to check (JSONL)")
    parser.add_argument("--existing", action="append", default=[], help="examples already accepted")
    parser.add_argument("--seeds", action="append", default=[], help="seed files the batch drew on")
    parser.add_argument("--benchset", help="AgentArmor benchset directory, to prove no overlap")
    parser.add_argument("--probes", help="probes.jsonl; the built-in probes if omitted")
    args = parser.parse_args(argv)

    _, probes = load_probes(args.probes)
    rows = [row for path in args.batch for row in read_jsonl(path)]
    existing = [row for path in args.existing for row in read_jsonl(path)]
    seeds = [seed["text"] for path in args.seeds for seed in read_jsonl(path)]
    benchmark = benchset_texts(args.benchset) if args.benchset else []
    problems, usable = check(rows, probes, seeds, benchmark, existing)
    for problem in problems:
        print(json.dumps(problem))
    summary = {
        "examples": len(rows),
        "groups": len({row["group"] for row in usable}),
        "families": dict(Counter(row["family"] for row in usable).most_common()),
        "facets": dict(Counter(facet for row in usable for facet in row["facets"])),
        "benign": sum(not row["facets"] for row in usable),
        "from_seeds": sum("seed" in row for row in usable),
        "checked_against": {"seeds": len(seeds), "benchset_cases": len(benchmark)},
        "problems": dict(Counter(problem["problem"] for problem in problems)),
    }
    print(json.dumps(summary))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
