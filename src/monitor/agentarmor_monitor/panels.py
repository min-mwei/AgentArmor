"""Requests for the decision model from packets that were built elsewhere. Nothing is sent.

    agentarmor-monitor panels packets.jsonl --out panels/

A measurement of the decision model compares packets: the same proposed call with more or
less shown of what led to it. Whoever builds the packets (Summarizer's
train/campaign_arms.py does) hands over, one record per line,

    {"run", "arm", "packet", "tool", "input", "pair", "kind", "decision"}

and this writes each as the request the monitor would make of it: the same rules, the same
layout of the state (policy.state), the call's arguments shown and redacted as a gate shows
a call it holds (escalate.proposed), and the registered decision question with its exact
option text (kai.QUESTIONS). Only "packet" differs between the arms of one run, so only the
packet is compared.

An arm's name becomes a file's name, so it is held to a plain word: a lowercase letter, then
lowercase letters, digits, "-" or "_", forty characters at most. Anything else is refused
before a single file is written: a name out of an input file never chooses where this
writes.

One file per arm is written to --out, which must be a new or an empty folder: <arm>.jsonl,
a row for each run in the order given,

    {"state", "questions": {"decision": {..., "label"}}, "_meta": {...}}

"label" is the record's "decision" ("allow" or "deny") when it has one. "_meta" says which
run, pair, kind and arm a row is, how long its state is, and whether its call is shown
whole. Nothing of a session is printed: counts only. The files hold session text and are
to be treated like a session's own log.

Nothing here decides whether a state fits the model. That is the reader's to say, with its
own tokenizer, and a state it does not admit is counted as that by whoever scores.
"""

import json
import re
from pathlib import Path

from .escalate import proposed, request_sha256
from .kai import DECISION, QUESTIONS
from .policy import LAYOUT, load_rules, state

NEEDED = ("run", "arm", "packet", "tool", "input")
ARM = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")  # what an arm may be called: it names a file


def read_packets(path):
    """The records of a packets file, each checked to hold what a request is made of."""
    rows = []
    with open(path, encoding="utf-8") as lines:
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = [name for name in NEEDED if name not in row]
            if not isinstance(row, dict) or missing:
                raise ValueError(f"{path}:{number}: a packet record needs {', '.join(NEEDED)}")
            if not isinstance(row["packet"], str) or not isinstance(row["arm"], str):
                raise ValueError(f"{path}:{number}: packet and arm are text")
            if not ARM.match(row["arm"]):
                raise ValueError(
                    f"{path}:{number}: an arm is a plain lowercase word, and this one is not"
                )
            rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no packet record")
    seen = set()
    for row in rows:
        if (row["run"], row["arm"]) in seen:
            raise ValueError(f"{path}: {row['run']} has two packets under the arm {row['arm']}")
        seen.add((row["run"], row["arm"]))
    return rows


def request_of(row, rules):
    """One packet record as a row of a panel: the request, its label and what it is."""
    tool, arguments, account = proposed({"tool": row["tool"]}, arguments=row["input"])
    body = state(rules, row["packet"], (tool, arguments))
    question = dict(QUESTIONS[DECISION])
    if row.get("decision") in question["criteria"]:
        question["label"] = row["decision"]
    meta = {"id": f"{row['run']}#{row['arm']}", "run": row["run"], "arm": row["arm"]}
    meta |= {"group_id": row.get("pair"), "kind": row.get("kind"), "layout": LAYOUT}
    meta |= {"evaluation_only": True, "training_eligible": False}
    meta |= {"state_chars": len(body), "packet_chars": len(row["packet"])}
    # Whole means every character of the call's own arguments: nothing clipped away.
    meta |= {"call_whole": not account["call_chars_not_shown"]}
    meta |= {"call_redactions": account["call_redactions"]}
    meta["request_sha256"] = request_sha256(body, (DECISION,))
    return {"state": body, "questions": {DECISION: question}, "_meta": meta}


def write(path, out, rules=None):
    """Write <arm>.jsonl under out for the packets at path. Returns counts, and no text."""
    rules = load_rules() if rules is None else rules
    rows = read_packets(path)
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"{out} is not a new or an empty folder")
    arms, told = {}, {"layout": LAYOUT, "question": DECISION, "rules": [r["id"] for r in rules]}
    for row in rows:
        arms.setdefault(row["arm"], []).append(request_of(row, rules))
    # Every row is made and every destination checked before anything is written: either
    # all the files are written, inside out, or none is.
    out.mkdir(parents=True, exist_ok=True)
    inside = out.resolve()
    places = {arm: out / f"{arm}.jsonl" for arm in arms}
    for arm, place in places.items():
        if not ARM.match(arm) or place.resolve().parent != inside:
            raise ValueError(f"the arm {arm!r} names no file of {out}")
    told["arms"] = {}
    for arm, made in arms.items():
        with open(places[arm], "x", encoding="utf-8") as handle:
            for request in made:
                handle.write(json.dumps(request, ensure_ascii=False) + "\n")
        sizes = sorted(request["_meta"]["state_chars"] for request in made)
        told["arms"][arm] = {
            "rows": len(made),
            "labelled": sum("label" in request["questions"][DECISION] for request in made),
            "state_chars_max": sizes[-1],
            "state_chars_median": sizes[len(sizes) // 2],
            "calls_not_whole": sum(not request["_meta"]["call_whole"] for request in made),
        }
    # The arms of one run differ in the packet and in nothing else: checked, not assumed.
    by_run = {}
    for made in arms.values():
        for request in made:
            packet_free = request["state"].replace(_packet_of(rows, request), "\x00")
            by_run.setdefault(request["_meta"]["run"], set()).add(packet_free)
    told["runs"] = len(by_run)
    told["runs_whose_arms_differ_beyond_the_packet"] = sum(
        len(found) > 1 for found in by_run.values()
    )
    return told


def _packet_of(rows, request):
    wanted = (request["_meta"]["run"], request["_meta"]["arm"])
    return next(row["packet"] for row in rows if (row["run"], row["arm"]) == wanted)
