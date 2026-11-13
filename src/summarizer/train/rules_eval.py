"""Measure the wording rules on evaluation files, along the path the summarizer serves.

    python train/rules_eval.py train/data/eval/pilot-001.jsonl train/data/eval/dev-002.jsonl \\
        --use development --fills 3

The wording rules (facts.text_spans) need no model. Each example's template is filled, redacted
and cut into chunks exactly as score_eval.py does. The rules are run over the whole served text,
as the runtime runs them, and a match belongs to the chunk it starts in. Reported:

- per facet: how often any rule that speaks for the facet fires on examples that have it and on
  examples that do not, and how often such a match overlaps the annotated span;
- per rule: the same two rates for its facet, and where it fires on examples without the facet,
  by the annotated address (agent, human, none) and by whether the text quotes or reports;
- the level the rules alone give an example: the highest of its chunks, with the priors combined
  as the runtime combines them. Probes, links to actions and observables raise levels further in
  a session; this is the rules' own share;
- per carrier: how many examples it has, how often the rules fire on those with a facet and on
  those without, and the share the rules alone put at low or higher and at medium or higher.
  That last pair is the load a carrier would send on. Most carriers have a handful of examples:
  the rows are denominators to read, not rates to compare.

A rule is scored against the facet its label falls under. That is a proxy: a facet is wider
than any one rule, so a rule about a claimed approval is scored against agent_directive. What a
facet says is that the pattern is present in the text. Whether the text is an injection in its
session, and whether a call should run, are other labels and are not measured here.

Where examples carry a "wording" annotation (docs/data-request-003.md, section 3), each rule
is also scored against its own wording: the entries a predicate over their fields picks out.
Reported per rule are how many such entries one of its matches overlaps, and how many of its
matches fall on such an entry and how many on none. A match on none is not called an error
here. It may be a wider indicator that is still right, an entry the annotator left out, or a
true non-match, and someone has to read it. Three rules match more than one thing and are
scored on a slice that is named. An example without the field is left out of these rows; one
with an empty list is in them, as text read and found to hold no such wording. An entry whose
span cannot be carried to the served text, because redaction moved it, is counted as evidence
that is missing and never as wording that is absent.

One more row accounts for every annotated entry by whether any rule claims it. Wording no
predicate picks out (a request to delete something, said as a bare order, is one) is in no
rule's row, so it is counted here: how many such entries, with which effects and forms, how
many were served, and how many of those some rule's match overlaps all the same. That last
count is what happened, not a claim that the rule is for that wording.

A rule that fires where the facet is absent is not an error. The rules favor recall, a quoted or
reported order still holds the wording, and the detector that reads the packet decides. These
counts say what a change to the rules buys and what it costs, before and after.

Rates average each example's share of its fills: filled variants are repeats of one example, not
more examples. Nothing printed holds text or an id, so whoever may read a test can run this on
it and share the result. A test is not scored from uncommitted code.
"""

import argparse
import hashlib
import json
from collections import Counter

from check_data import EFFECTS
from check_eval import FIELDS, FORMS, any_present, seeds_of, span_problem, wording_problems
from score_eval import VOCABULARY, carry, read_once, rows_of, serve, source_identity

from summarizer import facts
from summarizer.labels import CONTENT, combine, content_indicators
from summarizer.summarize import detector

# The facet each wording rule speaks for. Every rule has to be here: one that is not would be
# changed without being measured.
SPEAKS_FOR = {
    "agent_directive": (
        "override_text",
        "addressed_text",
        "responder_text",
        "trigger_text",
        "directive_text",
        "authority_text",
        "concealment_text",
        "third_person_text",
        "approval_text",
        "deferred_text",
        "called_text",
    ),
    "data_theft": ("exfiltration_text", "secret_text"),
    "control_tampering": ("bypass_text", "persistence_text"),
}
LEVELS = ("none", "low", "medium", "high")


def _effect(*names):
    return lambda entry: bool(set(entry["effects"]) & set(names))


def _form(*names):
    return lambda entry: bool(set(entry["forms"]) & set(names))


def _to_an_agent(*names):
    return lambda entry: entry["address"] == "agent" and _form(*names)(entry)


def _to_an_agent_by_name_or_in_the_third_person(entry):
    return entry["address"] == "agent" and _form("named_address", "third_person", "passive")(entry)


# A rule's own wording: the annotated entries it is scored against. Each predicate reads an
# entry's own fields and nothing else; no rule is ever run to decide what an entry is.
OWN = {
    "override_text": _effect("rule_or_role_change"),
    "approval_text": _effect("claimed_approval"),
    "bypass_text": _effect("bypass_check"),
    "concealment_text": _effect("concealment"),
    "persistence_text": _effect("persistence"),
    "trigger_text": _effect("later_trigger"),
    "deferred_text": _effect("later_trigger"),
    "exfiltration_text": _effect("send_out"),
    "secret_text": _effect("disclosure"),
    # Forms are grammar (docs/data-request-003.md, form vocabulary 2): these two rules are
    # about an assistant being named or described, so their target needs that address too.
    "called_text": _to_an_agent("named_address"),
    "directive_text": _form("direct"),
    "third_person_text": _to_an_agent("third_person", "passive"),
}
# Rules that match more than one thing: (what the slice is, its predicate).
SLICES = {
    "authority_text": (
        "authority impersonation or a claimed approval",
        _effect("authority_impersonation", "claimed_approval"),
    ),
    "addressed_text": (
        "to an agent, by name, in the third person or in the passive",
        _to_an_agent_by_name_or_in_the_third_person,
    ),
    "responder_text": (
        "reply manipulation, disclosure, or a change of rules or role",
        _effect("reply_manipulation", "disclosure", "rule_or_role_change"),
    ),
}


def rule_set():
    """(rule names, SHA-256 of the rules with their priors): what a count holds for."""
    names = [flag for flag, _ in facts._TEXT_FLAGS]
    mapped = [flag for flags in SPEAKS_FOR.values() for flag in flags]
    if sorted(mapped) != sorted(names):
        odd = ", ".join(sorted(set(mapped) ^ set(names)))
        raise SystemExit(f"SPEAKS_FOR must name every wording rule once, and only rules: {odd}")
    if sorted([*OWN, *SLICES]) != sorted(names):
        odd = ", ".join(sorted(set([*OWN, *SLICES]) ^ set(names)))
        raise SystemExit(f"OWN and SLICES must between them name every rule once: {odd}")
    spec = [
        (flag, pattern.pattern, pattern.flags, list(CONTENT[flag]))
        for flag, pattern in facts._TEXT_FLAGS
    ]
    spec.append(sorted(facts.BROAD_FORM_OF.items()))  # which weak match yields to which rule
    return names, hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def observe(rows, fills=1, seed=0, vocabulary="dev"):
    """What the rules did on each example, as shares of its fills, and the serving coverage.

    One record per example: {"fired": {rule: share}, "tagged": {facet: share},
    "on_span": {facet: share of the fills whose span could be carried}, "level": {name: share},
    "present": {facet: bool}, "address", "quoted", "block"}.
    """
    records, coverage = [], {"examples": len(rows), "fills": fills, "unscanned_chars": 0}
    coverage["redacted"] = 0
    for row in rows:
        fired = dict.fromkeys(facts_names(), 0)
        tagged, on_span = dict.fromkeys(SPEAKS_FOR, 0), dict.fromkeys(SPEAKS_FOR, 0)
        carried, level = dict.fromkeys(SPEAKS_FOR, 0), dict.fromkeys(LEVELS, 0)
        served_fills = []
        for item in serve(row, fills, seed, vocabulary):
            coverage["unscanned_chars"] += item["unscanned"]
            coverage["redacted"] += item["spans"] is None
            # A match belongs to the chunk it starts in; text no chunk holds was not served.
            served = [
                (start, end, flag, basis)
                for start, end, flag, basis in facts.text_spans(item["text"])
                if any(low <= start < high for low, high in item["chunks"])
            ]
            served_fills.append((item, served))
            for flag in {flag for _, _, flag, _ in served}:
                fired[flag] += 1
            best = 0
            for low, high in item["chunks"]:
                mine = {flag: basis for start, _, flag, basis in served if low <= start < high}
                best = max(best, LEVELS.index(combine(content_indicators(mine))[2]))
            level[LEVELS[best]] += 1
            for facet, names in SPEAKS_FOR.items():
                hits = [(start, end) for start, end, flag, _ in served if flag in names]
                tagged[facet] += bool(hits)
                if row["facets"][facet]["present"] and item["spans"] is not None:
                    first, last = item["spans"][facet]
                    carried[facet] += 1
                    on_span[facet] += any(start < last and first < end for start, end in hits)
        record = {"block": row["block"], "address": row["address"], "quoted": bool(row["quoted"])}
        record["carrier"] = row["carrier"]
        record["own"] = own_wording(row, served_fills) if "wording" in row else None
        record["claims"] = claims(row, served_fills) if "wording" in row else None
        record["present"] = {facet: bool(row["facets"][facet]["present"]) for facet in SPEAKS_FOR}
        record["fired"] = {flag: count / fills for flag, count in fired.items()}
        record["tagged"] = {facet: count / fills for facet, count in tagged.items()}
        record["on_span"] = {
            facet: on_span[facet] / carried[facet] for facet in SPEAKS_FOR if carried[facet]
        }
        record["level"] = {name: count / fills for name, count in level.items()}
        records.append(record)
    return records, coverage


def facts_names():
    return [flag for flag, _ in facts._TEXT_FLAGS]


def own_wording(row, served_fills):
    """Per rule, how its matches lie against its own annotated entries, summed over the fills.

    {rule: {"entries", "found", "on", "off", "unmapped"}}: its entries in the fills whose spans
    could be carried, how many of those a match overlaps, its matches on such an entry and on
    none, and its entries in the fills where redaction moved the text. Those last are evidence
    that is missing: they are in no other count, and neither are that fill's matches.
    """
    out = {}
    for flag in facts_names():
        chosen = OWN[flag] if flag in OWN else SLICES[flag][1]
        mine = [entry for entry in row["wording"] if chosen(entry)]
        tally = dict.fromkeys(("entries", "found", "on", "off", "unmapped"), 0)
        for item, served in served_fills:
            if item["redacted"]:
                tally["unmapped"] += len(mine)
                continue
            spans = [carry(entry["span"], item["edits"]) for entry in mine]
            hits = [(start, end) for start, end, name, _ in served if name == flag]
            tally["entries"] += len(spans)
            tally["found"] += sum(
                any(s < last and first < e for s, e in hits) for first, last in spans
            )
            on = sum(any(s < last and first < e for first, last in spans) for s, e in hits)
            tally["on"] += on
            tally["off"] += len(hits) - on
        out[flag] = tally
    return out


def claimed(entry):
    """Whether any rule's predicate picks this entry out, as its own wording or as its slice."""
    return any(chosen(entry) for chosen in (*OWN.values(), *(s[1] for s in SLICES.values())))


def claims(row, served_fills):
    """An example's annotated entries by whether any rule claims them.

    {"entries", "unclaimed", "effects", "forms", "carried", "under_a_match", "unmapped"}: how
    many entries it has and how many of them no predicate picks out; of those, the effects and
    forms they carry; and, summed over the fills, how many could be carried to the served text,
    how many of those any rule's match overlaps, and how many redaction moved.
    """
    free = [entry for entry in row["wording"] if not claimed(entry)]
    tally = {"entries": len(row["wording"]), "unclaimed": len(free)}
    tally["effects"] = Counter(name for entry in free for name in entry["effects"])
    tally["forms"] = Counter(name for entry in free for name in entry["forms"])
    tally |= dict.fromkeys(("carried", "under_a_match", "unmapped"), 0)
    for item, served in served_fills:
        if item["redacted"]:
            tally["unmapped"] += len(free)
            continue
        spans = [carry(entry["span"], item["edits"]) for entry in free]
        tally["carried"] += len(spans)
        tally["under_a_match"] += sum(
            any(start < last and first < end for start, end, _, _ in served)
            for first, last in spans
        )
    return tally


def claim_row(records, fills):
    """Every annotated entry, by whether any rule's predicate picks it out.

    An entry no rule claims is in none of the per-rule rows: this is where it is counted. It
    is neither a rule's miss nor a rule's hit. Examples without the annotation, examples
    annotated as holding none, claimed entries and unclaimed ones are four different counts.
    """
    told = [r["claims"] for r in records if r["claims"] is not None]
    row = {"wording_entries": "by whether any rule claims them"}
    row |= {"examples_without_wording": len(records) - len(told), "examples_annotated": len(told)}
    row["examples_with_none"] = sum(not item["entries"] for item in told)
    row["entries"] = sum(item["entries"] for item in told)
    row["unclaimed"] = sum(item["unclaimed"] for item in told)
    row["claimed"] = row["entries"] - row["unclaimed"]
    for key, names in (("effects", EFFECTS), ("forms", FORMS)):
        total = sum((item[key] for item in told), Counter())
        row[f"unclaimed_{key}"] = {name: total[name] for name in names if total[name]}
    # Per example, as in the per-rule rows: an entry seen in every fill counts once.
    carried = sum(item["carried"] for item in told)
    row["unclaimed_carried"] = round(carried / fills, 2)
    under = sum(item["under_a_match"] for item in told)
    row["unclaimed_under_a_match"] = round(under / carried, 3) if carried else None
    row["unclaimed_unmapped"] = round(sum(item["unmapped"] for item in told) / fills, 2)
    return row


def own_row(flag, records, fills):
    """One rule against its own wording, over the examples that carry the annotation."""
    told = [r["own"][flag] for r in records if r["own"] is not None]
    row = {"rule_on_its_wording": flag, "examples_annotated": len(told)}
    row["scored_on"] = "its own wording" if flag in OWN else f"a slice: {SLICES[flag][0]}"
    total = {key: sum(item[key] for item in told) for key in ("entries", "found", "on", "off")}
    # Counts are per example: an entry or a match seen in every fill counts once.
    row["entries"] = round(total["entries"] / fills, 2)
    row["found"] = round(total["found"] / total["entries"], 3) if total["entries"] else None
    row["matches_on"] = round(total["on"] / fills, 2)
    row["matches_on_none"] = round(total["off"] / fills, 2)
    row["entries_unmapped"] = round(sum(item["unmapped"] for item in told) / fills, 2)
    return row


def _rate(values):
    values = list(values)
    return round(sum(values) / len(values), 3) if values else None


def facet_row(facet, records):
    """How the rules that speak for one facet did on the examples with it and without it."""
    yes = [r for r in records if r["present"][facet]]
    no = [r for r in records if not r["present"][facet]]
    row = {"facet": facet, "present": len(yes), "absent": len(no)}
    row["recall"] = _rate(r["tagged"][facet] for r in yes)
    row["fpr"] = _rate(r["tagged"][facet] for r in no)
    mapped = [r for r in yes if facet in r["on_span"]]
    row["evidence_tagged"] = _rate(r["on_span"][facet] for r in mapped)
    return row | {"unmapped": len(yes) - len(mapped)}


def rule_row(flag, records):
    """Where one rule fires: with its facet, without it, and on what kind of text without it."""
    facet = next(name for name, flags in SPEAKS_FOR.items() if flag in flags)
    label, prior = CONTENT[flag]
    yes = [r for r in records if r["present"][facet]]
    no = [r for r in records if not r["present"][facet]]
    row = {"rule": flag, "facet": facet, "label": label, "prior": prior}
    row["fires_present"] = _rate(r["fired"][flag] for r in yes)
    row["fires_absent"] = _rate(r["fired"][flag] for r in no)
    row["absent_by_address"] = {
        address: {"examples": len(part), "fires": _rate(r["fired"][flag] for r in part)}
        for address in ("agent", "human", "none")
        if (part := [r for r in no if r["address"] == address])
    }
    quoted = [r for r in no if r["quoted"]]
    row["absent_quoted"] = {
        "examples": len(quoted),
        "fires": _rate(r["fired"][flag] for r in quoted),
    }
    return row


def level_row(records):
    """The level the rules alone give: examples with any facet, and examples with none."""

    def shares(part):
        at = {name: _rate(r["level"][name] for r in part) for name in LEVELS}
        row = {"examples": len(part), **at}
        if part:
            row["low_or_higher"] = round(1 - at["none"], 3)
            row["medium_or_higher"] = round(at["medium"] + at["high"], 3)
        return row

    some = [r for r in records if any(r["present"].values())]
    none = [r for r in records if not any(r["present"].values())]
    return {"levels": "rules alone", "with_a_facet": shares(some), "with_no_facet": shares(none)}


def carrier_rows(records):
    """Per carrier: its examples, where the rules fire, and the load the rules alone send on."""
    rows = []
    for carrier in sorted({r["carrier"] for r in records}):
        mine = [r for r in records if r["carrier"] == carrier]
        some = [r for r in mine if any(r["present"].values())]
        none = [r for r in mine if not any(r["present"].values())]
        row = {"carrier": carrier, "examples": len(mine), "with_a_facet": len(some)}
        row["fires_with"] = _rate(1 - r["level"]["none"] for r in some)
        row["fires_without"] = _rate(1 - r["level"]["none"] for r in none)
        row["low_or_higher"] = _rate(1 - r["level"]["none"] for r in mine)
        row["medium_or_higher"] = _rate(r["level"]["medium"] + r["level"]["high"] for r in mine)
        rows.append(row)
    return rows


def report(records, fills=1):
    """Every row for a set of records: per facet, per rule, the levels, per carrier, and, where
    examples are annotated for it, each rule against its own wording and the entries no rule
    claims."""
    rows = [facet_row(facet, records) for facet in SPEAKS_FOR]
    rows += [rule_row(flag, records) for flag in facts_names()]
    rows += [level_row(records), *carrier_rows(records)]
    if any(record["own"] is not None for record in records):
        rows += [own_row(flag, records, fills) for flag in facts_names()]
        rows.append(claim_row(records, fills))
    return rows


def checked(row):
    """Whether a row has what this tool reads, in the shape check_eval.py passes.

    It is no substitute for that check, which also needs the seeds and the blocks. It keeps a
    file that was never checked, or an annotation the checker refuses, from being scored.
    """
    if not isinstance(row, dict) or seeds_of(row) is None:
        return False
    if any(not isinstance(row.get(key), kind) for key, kind in FIELDS.items()):
        return False
    for facet in SPEAKS_FOR:
        note = row["facets"].get(facet)
        if not isinstance(note, dict) or not isinstance(note.get("present"), bool):
            return False
        if note["present"] and span_problem(note.get("span"), row["text"], "a present facet"):
            return False
    if "wording" not in row:
        return True
    return not wording_problems(row["wording"], row["text"], any_present(row))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", help="evaluation examples that passed check_eval.py")
    parser.add_argument("--use", choices=tuple(VOCABULARY), required=True)
    parser.add_argument("--fills", type=int, default=3, help="entity fills per example")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--code", help="the source commit, when this is not a Git checkout")
    args = parser.parse_args(argv)

    source = source_identity(args.use, args.code)  # before any text is read
    names, digest = rule_set()
    rows, files = [], []
    for name in args.files:
        data, sha = read_once(name)
        found = rows_of(data)
        if not all(checked(row) for row in found):
            raise SystemExit(f"{name}: run train/check_eval.py on this file first")
        rows += found
        files.append({"file": name, "sha256": sha, "examples": len(found)})
    records, coverage = observe(rows, args.fills, args.seed, VOCABULARY[args.use])
    run = {"tool": "rules_eval", "use": args.use, "fills": args.fills, "seed": args.seed}
    run |= {"files": files, "rules": names, "rules_sha256": digest} | source
    run["scored_against"] = "the facet each rule's label falls under; a proxy for its own pattern"
    run["detector"] = detector()  # what a summary made by this code would name
    coverage["carriers"] = len({row["carrier"] for row in rows})
    coverage["examples_without_wording"] = sum("wording" not in row for row in rows)
    print(json.dumps({"run": run, "coverage": coverage}))
    for row in report(records, args.fills):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
