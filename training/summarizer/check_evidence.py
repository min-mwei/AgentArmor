"""Whether the facts a root's labels rest on are evidence the admission check can lose and detect.

    PYTHONPATH=src/summarizer:training/summarizer SUMMARIZER_HOME=<scratch> \\
        python training/summarizer/check_evidence.py ROOTS

A training root labels its decision and the five rules of the monitor's policy, and names in
`story.evidence`, for `decision` and for each rule, the ids of the declared facts that label
rests on (`[]` for a label resting on the proposed call alone, which every state shows whole).
A view is admitted for training only when the scorer finds every declared fact in it, so a
fact a label needs must be one whose absence the scorer notices (MR165-2).

For every root and each agent layout this replays the root (hash stand-in encoder), writes
its reference packet (every declared fact, bridge and limit, at the checker's reference
budget) and requires it to pass the scorer (campaign-contract/13). Then, for every fact
`story.evidence` names, it removes that fact's event from the packet: its lines and shown
entries in the session under decision, or its lines and history items under EARLIER SESSIONS.
The removal is noticed only when, fact by fact and whatever the readiness or route, the scorer
found the fact in the reference packet and does not find it afterwards, and the packet no
longer passes (MR168-1). For a campaign that expects complete evidence that means losing the
fact stops its admission; for one whose earlier sessions have a gap (it expects its history to
be incomplete), version 13 requires it to keep every earlier fact the gap did not take, so the
loss is caught without asking a correct refusal to change. A named fact the gap took cannot be
shown at all and is listed under `review`. The gold labels are never changed.

Refused (one line per root, exit status 1): a labelled root without `story.evidence`, or with
other keys than `decision` and the rules it labels, or naming a fact it does not declare; a
reference packet that does not pass; a named fact whose removal the scorer does not notice.
Declared facts that no label names are listed under `review` with whether their removal is
noticed: a person decides whether a label should rest on them. A root without `story.rules`
is a held root and is only counted. The last line counts everything and names the bytes
checked, this file's hash and the scorer.
"""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from campaign_contract import AGENTS, SCORERS, layout, reference, replay, score

RULES = (
    "external-instructions",
    "secrets",
    "destructive-scope",
    "unestablished-authorization",
    "delegated-task",
)
VERSION = 13  # the scorer that keeps a gap campaign's available evidence (MR168-1)
BUDGET = 20000  # the checker's reference budget (check_campaigns.REFERENCE_BUDGET)


def without(view, laid, fact):
    """The packet with one fact's event taken out: its lines and its shown or history entries."""
    part = next(p for p in laid["parts"] if p["id"] == fact["session"])
    seq = part["laid"]["where"][fact["step"]][fact["event"]]
    altered = copy.deepcopy(view)
    lines = altered["text"].splitlines()
    if part["mine"]:
        kept = [line for line in lines if not line.startswith(f"#{seq} ")]
        altered["shown"] = [item for item in altered.get("shown", ()) if item.get("seq") != seq]
    else:
        tag, mark = f"[s:{part['alias']}] ", re.compile(rf"#{seq}(?!\d)")
        kept = [line for line in lines if not (line.startswith(tag) and mark.search(line))]
        history = altered.get("history") or {}
        history["items"] = [
            item
            for item in history.get("items", ())
            if not (item.get("alias") == part["alias"] and item.get("seq") == seq)
        ]
    removed = len(lines) - len(kept)
    altered["text"] = "\n".join(kept)
    return altered, removed


def label_problems(root):
    """Problems of a labelled root's `story.evidence` itself, before any replay."""
    story = root.get("story") or {}
    evidence, facts = story.get("evidence"), {fact.get("id") for fact in root.get("facts", ())}
    if not isinstance(evidence, dict):
        return ["no story.evidence: the facts each label rests on"]
    if set(evidence) != {"decision", *story["rules"]}:
        return ["story.evidence names exactly decision and the rules the root labels"]
    found = []
    for label, ids in evidence.items():
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            found.append(f"story.evidence.{label} is a list of fact ids")
            continue
        found += [
            f"story.evidence.{label} names {i}, which is not a declared fact"
            for i in ids
            if i not in facts
        ]
    return found


def check(root, runtime):
    """(problems, review, counts) of one root."""
    story = root.get("story") or {}
    if not isinstance(story.get("rules"), dict):
        return [], [], {"held": 1}
    problems = label_problems(root)
    if problems:
        return problems, [], {"labelled": 1}
    named = sorted({i for ids in story["evidence"].values() for i in ids})
    facts = {fact["id"]: fact for fact in root["facts"]}
    review, counts = (
        [],
        {"labelled": 1, "named_facts": len(named), "omissions_refused": 0, "omissions": 0},
    )
    for agent in AGENTS:
        laid = layout(root, agent)
        summaries, _ = replay(laid, runtime)
        view = reference(laid, summaries, BUDGET)
        before = score(laid, summaries, view, BUDGET, VERSION)
        if not before["passes"]:
            problems.append(f"its reference packet does not pass ({agent})")
            continue
        for name in sorted(facts):
            if not before["available"][name]:
                review.append(
                    f"fact {name} lies where the gap took the log: it cannot be shown ({agent})"
                )
                continue
            altered, removed = without(view, laid, facts[name])
            after = score(laid, summaries, altered, BUDGET, VERSION)
            noticed = removed > 0 and before["support"][name] and not after["support"][name]
            noticed = noticed and not after["passes"]
            if name in named:
                counts["omissions"] += 1
                counts["omissions_refused"] += noticed
                if not noticed:
                    labels = sorted(k for k, ids in story["evidence"].items() if name in ids)
                    if not removed:
                        how = "removes nothing"
                    elif not before["support"][name]:
                        how = "finds it not shown even before"
                    elif after["support"][name]:
                        how = "leaves it found"
                    else:
                        how = "still passes"
                    problems.append(
                        f"removing fact {name} ({', '.join(labels)}) from the reference packet "
                        f"{how} ({agent})"
                    )
            else:
                review.append(
                    f"fact {name} is named by no label; its removal is {'noticed' if noticed else 'not noticed'} ({agent})"
                )
    return problems, review, counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("roots", help="the training roots (JSONL)")
    args = parser.parse_args(argv)
    from summarizer.embedder import create
    from summarizer.summarize import Runtime

    data = Path(args.roots).read_bytes()
    roots = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    runtime = Runtime(create("hash"))
    totals, failing = {}, 0
    for root in roots:
        problems, review, counts = check(root, runtime)
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value
        if problems or review:
            print(json.dumps({"id": root.get("id"), "problems": problems, "review": review}))
        failing += bool(problems)
    print(
        json.dumps(
            {
                "roots": len(roots),
                "refused": failing,
                **totals,
                "file_sha256": hashlib.sha256(data).hexdigest(),
                "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "scorer": SCORERS[VERSION],
                "budget": BUDGET,
            }
        )
    )
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
