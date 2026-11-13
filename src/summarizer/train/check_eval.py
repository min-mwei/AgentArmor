"""Check an evaluation file for the probes, and write the manifest the trainer may read.

    python train/check_eval.py train/data/eval/pilot-001.jsonl --use development \\
        --seeds train/data/seeds/all.jsonl --blocks train/data/eval/blocks.jsonl

An evaluation example carries its annotations beside the text (train/README.md, "An independent
test for the probes"). This checks what can be checked without reading meaning into the text:

- the documented fields and types, and unique ids;
- every seed reference resolves, to a seed of the example's own block, and that block is
  assigned to the use the file is for (development or test), never to training;
- every facet is annotated, a present facet has a span inside the text, zero-based and
  end-exclusive, that does not start or end inside a slot, and an absent one has none;
- a test's ids do not state a label (they are shared in the manifest);
- no example shares eight words in a row with the benchset (when --benchset is given), or with
  any seed unless a reviewer has allowed that exact overlap (--allow): shared words are
  evidence of overlap, and someone has to say whether it is an envelope or copied content;
- the text gives nothing away: no probe id or label, no credential format, no invisible
  character, no literal host, address or mailbox.

Two findings are flagged for a reader and are not failures (they carry "review": true): a run
of six words used in more than four groups, and an example with more than a quarter of its
runs also in another group. A file stamped from a few clauses shows both; so can honest texts
that share a tool's fixed envelope. The counts cannot tell them apart, and passing them proves
nothing about independence. A manifest for a flagged file is written only with --reviewed,
which records who read the flagged examples and why they stand.

With --manifest it also writes one record per example without its text or its annotations, and
a record of counts, so that a test's shape can be shared while its content stays unread.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from check_data import (
    _ANSWER_KEY,
    _SLOT,
    EFFECTS,
    PHRASE,
    PHRASE_GROUPS,
    RUN,
    benchset_texts,
    runs,
)
from common import load_probes, read_jsonl, write_jsonl

from summarizer.events import chunk_spans
from summarizer.facts import hidden_text, observables, visible
from summarizer.redact import redact

FIELDS = {"id": str, "block": str, "group": str, "carrier": str, "writer": str, "text": str}
FIELDS |= {"facets": dict, "address": str, "quoted": bool}
ADDRESS = ("agent", "human", "none")
# The wording annotation (docs/data-request-003.md, section 3): what a piece of steering wording
# is, in words about the wording. It never names a rule. A file may lack the field; that is not
# the same as annotating an empty list, which says the text was read and holds none.
FORMS = (
    "named_address",
    "direct",
    "imperative",
    "third_person",
    "passive",
    "marker",
    "declarative",
)
KINDS = ("form_only", "added_content")  # of reply manipulation, fixed when the example is written
ENTRY = ("span", "effects", "forms", "address", "quoted", "kind")
# Shared wording is a signal for a reader, not a failure: a tool's fixed envelope repeats in
# honest data, and so does a clause list. Someone has to look and say which it is.
REVIEW = ("repeated_phrasing", "shared_wording", "copies_seed_allowed")
WORDING = ("repeated_phrasing", "shared_wording")  # these need a --reviewed note for a manifest
SHARED_SHARE = 0.25  # of an example's six-word runs, how many may also occur in another group
# Words that would make a shared id an answer key.
_LABEL_ID = re.compile(
    r"match|benign|attack|malicious|positive|negative|present|absent|directive|theft|tamper", re.I
)


def seeds_of(row):
    """The seed ids an example names: one under "seed", or several under "seeds"."""
    one, many = row.get("seed"), row.get("seeds")
    if isinstance(one, str) and many is None:
        return [one]
    if (
        isinstance(many, list)
        and many
        and all(isinstance(item, str) for item in many)
        and one is None
    ):
        return many
    return None


def overlap(text, seed_runs):
    """(count, fingerprint) of the runs of RUN words a text shares with the seeds; (0, "") if none.

    The fingerprint is a hash of exactly which runs match, so an allowance given for one set of
    matches does not cover another.
    """
    shared = sorted(" ".join(run) for run in runs(text, RUN) & seed_runs)
    if not shared:
        return 0, ""
    return len(shared), hashlib.sha256("\n".join(shared).encode("utf-8")).hexdigest()


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


ALLOWANCE = ("id", "text_sha256", "runs_sha256", "corpus_sha256", "reason", "reviewer")


def well_formed(note):
    """An allowance is six non-empty strings: nothing else stands for a completed review."""
    return isinstance(note, dict) and all(
        isinstance(note.get(key), str) and note[key].strip() for key in ALLOWANCE
    )


def allowed(row, fingerprint, allowances, corpus):
    """The allowance that covers this row's overlap, or None.

    It must name this row, this text, these matched runs and this seed corpus, and say who
    reviewed it and why. Anything that has changed since needs a fresh review.
    """
    note = (allowances or {}).get(row["id"])
    if not well_formed(note):
        return None
    same = note["text_sha256"] == text_sha256(row["text"])
    same = same and note["runs_sha256"] == fingerprint and note["corpus_sha256"] == corpus
    return note if same else None


def present(note):
    """Whether a facet's annotation says the pattern is there. A malformed one says nothing."""
    return isinstance(note, dict) and note.get("present") is True


def span_problem(span, text, what):
    """What is wrong with a [start, end) span over a template, or None."""
    inside = (
        isinstance(span, list)
        and len(span) == 2
        and all(isinstance(edge, int) and not isinstance(edge, bool) for edge in span)
        and 0 <= span[0] < span[1] <= len(text)
    )
    if not inside:
        return f"{what} needs [start, end) inside the text"
    # A span edge inside a slot has no place in the filled text.
    if any(slot.start() < edge < slot.end() for slot in _SLOT.finditer(text) for edge in span):
        return "a span must not start or end inside a slot"
    return None


def any_present(row):
    return any(present(note) for note in row["facets"].values())


def wording_problems(wording, text, facet_present):
    """What is wrong with an example's wording annotation: a list of sentences, empty if none.

    Entries may overlap, and one text may hold entries with different addresses: an order to
    the agent beside a quotation of another. What is checked is each entry's own shape.
    """
    if not isinstance(wording, list):
        return ["wording is a list of entries; an empty list says the text holds none"]
    found = []
    if facet_present and not wording:
        found.append("a text with a facet present has at least one wording entry")
    for number, entry in enumerate(wording):
        where = f"entry {number}"
        if not isinstance(entry, dict) or sorted(entry) != sorted(ENTRY):
            found.append(f"{where}: needs exactly {list(ENTRY)}")
            continue
        for key, allowed in (("effects", EFFECTS), ("forms", FORMS)):
            values = entry[key]
            # Names are checked to be strings before anything hashes or compares them.
            names = isinstance(values, list) and all(isinstance(value, str) for value in values)
            if not names or not values or len(set(values)) != len(values):
                found.append(f"{where}: {key} is a list of one or more distinct names")
            elif odd := [value for value in values if value not in allowed]:
                found.append(f"{where}: unknown {key} {odd}")
        if entry["address"] not in ADDRESS:
            found.append(f"{where}: address must be one of {ADDRESS}")
        if not isinstance(entry["quoted"], bool):
            found.append(f"{where}: quoted is true or false")
        replies = isinstance(entry["effects"], list) and "reply_manipulation" in entry["effects"]
        if replies and entry["kind"] not in KINDS:
            found.append(f"{where}: reply manipulation needs kind, one of {KINDS}")
        elif not replies and entry["kind"] is not None:
            found.append(f"{where}: kind is for reply manipulation only")
        if said := span_problem(entry["span"], text, "an entry"):
            found.append(f"{where}: {said}")
    return found


def check(rows, probes, seeds, blocks, use, sources=(), benchmark=(), allowances=None, corpus=""):
    """Problems as {"id", "problem", "detail"}. seeds maps seed id to block, blocks block to use.

    sources are seed texts and benchmark the benchset's. Sharing RUN words in a row with a seed
    is evidence of overlap with the source, not proof of copying: a tool's fixed envelope and
    conventional syntax repeat in honest writing. So it blocks (copies_seed) until a reviewer
    has read the match and allowed it for that exact row, text, set of matched runs and seed
    corpus (allowances, keyed by example id); an allowed one is still listed, as a review note.
    Overlap with the benchset is never allowed.
    """
    seed_runs = set().union(*(runs(text, RUN) for text in sources)) if sources else set()
    bench_runs = set().union(*(runs(text, RUN) for text in benchmark)) if benchmark else set()
    problems, seen = [], set()
    facets = [probe["id"] for probe in probes]
    answers = {name.lower() for probe in probes for name in (probe["id"], probe["label"])}

    def report(row, problem, detail=""):
        found = {"id": str(row.get("id")), "problem": problem, "detail": str(detail)[:160]}
        problems.append(found | ({"review": True} if problem in REVIEW else {}))

    for row in rows:
        if not isinstance(row, dict):
            report({}, "format", "a line is one JSON object")
            continue
        wrong = [key for key, kind in FIELDS.items() if not isinstance(row.get(key), kind)]
        known = (*FIELDS, "seed", "seeds", "wording")
        wrong += [key for key in row if key not in known]
        if wrong:
            report(row, "format", f"missing, mistyped or unknown fields: {sorted(set(wrong))}")
            continue
        if row["id"] in seen:
            report(row, "duplicate_id")
        seen.add(row["id"])
        text = row["text"]
        named = seeds_of(row)
        if named is None:
            report(row, "seed_missing", "give seed, or seeds as a non-empty list")
        else:
            unknown = [seed for seed in named if seed not in seeds]
            foreign = [seed for seed in named if seeds.get(seed, row["block"]) != row["block"]]
            if unknown:
                report(row, "seed_unknown", unknown)
            if foreign:
                report(row, "seed_of_another_block", foreign)
        if use == "test" and (_LABEL_ID.search(row["id"]) or _LABEL_ID.search(row["group"])):
            report(row, "id_states_a_label", "a test's ids are shared; use neutral ones")
        if blocks.get(row["block"]) != use:
            report(
                row, "block_not_for_this_use", f"{row['block']} is for {blocks.get(row['block'])}"
            )
        if row["address"] not in ADDRESS:
            report(row, "format", f"address must be one of {ADDRESS}")
        if sorted(row["facets"]) != sorted(facets):
            report(row, "facets", f"annotate exactly {facets}")
            continue
        for name, note in row["facets"].items():
            span, said = note.get("span") if isinstance(note, dict) else ..., None
            if not isinstance(note, dict) or not isinstance(note.get("present"), bool):
                said = "needs present, span and why"
            elif not isinstance(note.get("why"), str) or not note["why"].strip():
                said = "needs a reason"
            elif note["present"]:
                said = span_problem(span, text, "a present facet")
            elif span is not None:
                said = "an absent facet has no span"
            if said:
                report(row, "span", f"{name}: {said}")
        if "wording" in row:
            for said in wording_problems(row["wording"], text, any_present(row)):
                report(row, "wording", said)
        told = sorted(name for name in answers if name in text.lower())
        if told or _ANSWER_KEY.search(text):
            report(row, "states_its_labels", told or _ANSWER_KEY.search(text)[0])
        if kinds := redact(text)[1]:
            report(row, "secret_format", sorted(kinds))
        if hidden_text(text) or visible(text) != text:
            report(row, "hidden_characters")
        if literal := sorted({value for _, _, value in observables(_SLOT.sub(" ", text))}):
            report(row, "literal_entity", f"use a slot instead of {literal}")
        mine = runs(text, RUN)
        shared, fingerprint = overlap(text, seed_runs)
        if shared and (note := allowed(row, fingerprint, allowances, corpus)):
            detail = f"{shared} runs of {RUN} words are in a seed; allowed by {note['reviewer']}"
            report(row, "copies_seed_allowed", detail)
        elif shared:
            report(row, "copies_seed", f"{shared} runs of {RUN} words are in a seed")
        if mine & bench_runs:
            report(row, "copies_benchset", f"shares {RUN} words in a row with a benchset case")
    # A run of words shared by many groups means the file was stamped from a few clauses: its
    # examples are then repeats, not samples. The wording is not quoted, since a test is private.
    phrases = {}
    usable = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("text"), str)
        and isinstance(row.get("group"), str)
    ]
    for row in usable:
        for run in runs(row["text"], PHRASE):
            phrases.setdefault(run, set()).add(row["group"])
    for row in usable:
        most = max((len(phrases[run]) for run in runs(row["text"], PHRASE)), default=0)
        if most > PHRASE_GROUPS:
            report(row, "repeated_phrasing", f"a {PHRASE}-word run of it is in {most} groups")
        # Keeping every clause just under that limit is still stamping: most of an example's
        # wording must be its own, whatever the count of groups it is shared with.
        mine = runs(row["text"], PHRASE)
        shared = sum(len(phrases[run]) > 1 for run in mine)
        if mine and shared / len(mine) > SHARED_SHARE:
            detail = f"{shared} of its {len(mine)} {PHRASE}-word runs are also in another group"
            report(row, "shared_wording", detail)
    return problems


def manifest(rows, probes):
    """What may be shared about an evaluation file: per example no text and no annotation.

    It is given rows that have the documented fields and facets. An annotation that check()
    reports a problem in is counted as that and as nothing else: a facet note that is not one
    is not a facet present, and a wording annotation with a problem adds no entry, effect or
    form to the counts.
    """
    out = []
    for row in rows:
        text = row["text"].encode("utf-8", "surrogatepass")
        item = {key: row[key] for key in ("id", "block", "group", "carrier", "writer")}
        item |= {"seeds": seeds_of(row) or [], "chars": len(row["text"])}
        item |= {"chunks": len(chunk_spans(row["text"])[0])}
        out.append(item | {"sha256": hashlib.sha256(text).hexdigest()})
    counts = {"examples": len(rows), "groups": len({row["group"] for row in rows})}
    counts |= {"blocks": dict(Counter(row["block"] for row in rows))}
    counts |= {"carriers": len({row["carrier"] for row in rows})}
    counts |= {"writers": sorted({row["writer"] for row in rows})}
    counts["distinct_texts"] = len({re.sub(r"\s+", " ", row["text"]).strip() for row in rows})
    # Aggregate label counts only: enough to see the balance, nothing that labels an example.
    counts["present"] = {
        probe["id"]: sum(present(row["facets"][probe["id"]]) for row in rows) for probe in probes
    }
    annotated = [row for row in rows if "wording" in row]
    if annotated:  # how much of each kind of wording there is, and how much is unannotated
        sound = [
            row
            for row in annotated
            if not wording_problems(row["wording"], row["text"], any_present(row))
        ]
        entries = [entry for row in sound for entry in row["wording"]]
        told = {"examples_annotated": len(annotated), "entries": len(entries)}
        told["examples_with_none"] = sum(not row["wording"] for row in sound)
        if len(sound) < len(annotated):  # never in a manifest: one is written without problems
            told["examples_with_problems"] = len(annotated) - len(sound)
        told["effects"] = dict(Counter(name for entry in entries for name in entry["effects"]))
        told["forms"] = dict(Counter(name for entry in entries for name in entry["forms"]))
        told["kinds"] = dict(Counter(entry["kind"] for entry in entries if entry["kind"]))
        counts["wording"] = told
    return out + [{"summary": counts}]


def sources_of(paths):
    """(seed id -> block, seed texts, corpus digest, ids of seeds without text), read once.

    A seed is an excerpt: one with no text cannot be checked against, so it is reported and
    the source check is not considered done.
    """
    seeds, texts, digests, empty = {}, [], [], []
    for path in paths:
        data = Path(path).read_bytes()
        digests.append(hashlib.sha256(data).hexdigest())
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                seed = json.loads(line)
                seeds[seed["id"]] = seed["block"]
                if isinstance(seed.get("text"), str) and seed["text"].strip():
                    texts.append(seed["text"])
                else:
                    empty.append(str(seed["id"]))
    corpus = (
        digests[0] if len(digests) == 1 else hashlib.sha256("".join(digests).encode()).hexdigest()
    )
    return seeds, texts, corpus, empty


def eligible(rows, probes, use, seed_paths, blocks_path, allow=None, benchset=None):
    """The one check a file passes before it is fitted on or frozen.

    Returns {"problems", "corpus", "seed_runs", "allowances_sha256"}. Seed files and the
    allowance file are each read once; the digests returned are of the bytes that were used.
    """
    seeds, sources, corpus, empty = sources_of(seed_paths)
    blocks = {row["block"]: row["use"] for row in read_jsonl(blocks_path)}
    allowances, receipt, malformed = None, None, []
    if allow:
        data = Path(allow).read_bytes()
        receipt = hashlib.sha256(data).hexdigest()
        notes = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
        malformed = [note for note in notes if not well_formed(note)]
        allowances = {note["id"]: note for note in notes if well_formed(note)}
    benchmark = benchset_texts(benchset) if benchset else ()
    problems = check(rows, probes, seeds, blocks, use, sources, benchmark, allowances, corpus)
    for note in malformed:
        name = str(note.get("id")) if isinstance(note, dict) else "?"
        detail = f"an allowance needs non-empty strings for {', '.join(ALLOWANCE)}"
        problems.append({"id": name, "problem": "allowance_format", "detail": detail})
    if empty:
        detail = f"{len(empty)} seeds have no text to check against, such as {empty[:3]}"
        problems.append({"id": "seeds", "problem": "source_incomplete", "detail": detail})
    seed_runs = set().union(*(runs(text, RUN) for text in sources)) if sources else set()
    out = {"problems": problems, "corpus": corpus, "seed_runs": seed_runs}
    return out | {"allowances_sha256": receipt}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", help="evaluation examples (JSONL)")
    parser.add_argument("--use", choices=("development", "test"), required=True)
    parser.add_argument("--seeds", action="append", required=True, help="seed files")
    parser.add_argument("--blocks", required=True, help="the block assignment (JSONL)")
    parser.add_argument("--manifest", help="write the shareable manifest here")
    parser.add_argument("--benchset", help="AgentArmor benchset: nothing may repeat it")
    parser.add_argument("--probes", help="probes.jsonl; the built-in probes if omitted")
    parser.add_argument("--allow", help="reviewed allowances for seed overlap (JSONL)")
    parser.add_argument(
        "--overlaps",
        help="write, for each example that overlaps a seed, the fingerprints an allowance must "
        "carry (no text): the reviewer adds a reason and a name to the ones that stand",
    )
    parser.add_argument(
        "--reviewed",
        help="who read the examples flagged for shared wording, and why they stand (recorded "
        "in the manifest; required to write one when anything is flagged)",
    )
    args = parser.parse_args(argv)

    _, probes = load_probes(args.probes)
    rows = read_jsonl(args.file)
    gate = eligible(rows, probes, args.use, args.seeds, args.blocks, args.allow, args.benchset)
    problems, corpus, seed_runs = gate["problems"], gate["corpus"], gate["seed_runs"]
    for problem in problems:
        print(json.dumps(problem))
    if args.overlaps:
        found = []
        for row in (row for row in rows if isinstance(row, dict)):
            text = row.get("text") if isinstance(row.get("text"), str) else ""
            count, fingerprint = overlap(text, seed_runs)
            if count and isinstance(row.get("id"), str):  # an allowance names a row by its id
                item = {"id": row["id"], "text_sha256": text_sha256(row["text"]), "runs": count}
                found.append(item | {"runs_sha256": fingerprint, "corpus_sha256": corpus})
        write_jsonl(args.overlaps, found)
    errors = [problem for problem in problems if not problem.get("review")]
    flagged = dict(Counter(p["problem"] for p in problems if p.get("review")))
    wording = {name: count for name, count in flagged.items() if name in WORDING}
    usable = not any(problem["problem"] in ("format", "facets") for problem in problems)
    summary = manifest(rows, probes)[-1]["summary"] if usable else {"examples": len(rows)}
    counts = {"problems": dict(Counter(p["problem"] for p in errors)), "review": flagged}
    counts["seed_corpus_sha256"] = corpus
    if args.allow:
        counts["allowances_sha256"] = gate["allowances_sha256"]  # of the bytes that were applied
    if wording:
        counts["reviewed"] = args.reviewed
    print(json.dumps(summary | counts))
    if args.manifest and not errors:
        if wording and not args.reviewed:
            print(
                "no manifest: shared wording was flagged and no --reviewed note says why it stands"
            )
            return 1
        shared = manifest(rows, probes)
        shared[-1]["summary"] |= {key: counts[key] for key in counts if key != "problems"}
        write_jsonl(args.manifest, shared)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
