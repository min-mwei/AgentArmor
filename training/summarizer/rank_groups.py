"""Which found evidence a packet would carry under four orders, counted against a
development file's named facts. A diagnostic: it changes no packet and decides nothing.

    python training/summarizer/rank_groups.py CAMPAIGNS [--embedder hash|embeddinggemma]
        [--room 1000 1500 2000 2500 3000] [--skip PAIR ...]

For every run (a campaign laid out for one agent, the first replay) the earlier sessions'
evidence is what summarizer.history.select() finds, in its own whole groups. A group is
taken whole or not at all, as a packet takes it. Four orders give room to the groups:

- selected: as the packet does now (key evidence, what bears, then context);
- recent: the group whose newest entry is nearest the call first;
- words: full-text score (SQLite FTS5, bm25) of a group's text against the call's own text;
- encoder: the largest similarity of the call, as a query, to one of the group's entries.

The query is the proposed call as the server sees it before it runs, and nothing else: no
fact text, route, decision or label reaches an order. A named fact is retained when the
entry that holds it (found by the scorer's own locate(), by identity) is in a group that
was taken. A fact in no found group is a miss under every order, and is counted as one.
Entries no campaign names are unlabelled; nothing here calls them irrelevant.

Costs are characters of each source's own lines plus a fixed allowance for the tie words,
which is close to a packet's own count and is not it. Counts only are printed.
"""

import argparse
import json
import sqlite3
from collections import Counter

import numpy as np

import campaign_contract as cc
import case_contract as contract
from common import read_jsonl
from summarizer import history
from summarizer.embedder import create
from summarizer.summarize import Runtime
from summarizer.text import entry_lines

ORDERS = ("selected", "recent", "words", "encoder")
TIE = 50  # characters allowed for an alias and the words of a tie on each line


def found_groups(laid, summaries):
    """(what select() finds, the call entry, {part id: part}) for one laid-out run."""
    deciding = laid["deciding"]

    def known(part):
        fields = {"key": part["id"], "summary": summaries[part["id"]]}
        return fields | {"cwd": part["cwd"], "namespace": part["namespace"]}

    others = [known(part) for part in laid["parts"] if not part["later"] and not part["mine"]]
    found = history.select(known(deciding), deciding["laid"]["call"], others, cc.HOME)
    entries = summaries[deciding["id"]]["entries"]
    call = next(
        e for e in entries if e.get("call_id") == deciding["laid"]["call"] and e["type"] == "action"
    )
    return found, call


def labels(laid, summaries):
    """[(part id, seq, type) or None] for every named fact of an earlier session: where
    the scorer's locate() finds it, or None when no entry of the summary holds it."""
    parts = {part["id"]: part for part in laid["parts"]}
    wanted = []
    for fact in laid["campaign"]["facts"]:
        part = parts[fact["session"]]
        if part["mine"]:
            continue
        mine = next(f for f in part["laid"]["case"]["facts"] if f["id"] == fact["id"])
        for member in contract.members(mine):
            entry, _ = contract.locate(part["laid"], summaries[part["id"]], member)
            wanted.append((part["id"], entry["seq"], entry["type"]) if entry else None)
    return wanted


def measured(found, call, summaries, embedder):
    """Each group with its items, cost, newest time, text and the two scores."""
    texts, cost, stamp = {}, Counter(), {}
    for key in {item["key"] for item in found["items"]}:
        for about, line in entry_lines(summaries[key]):
            cost[(key, about["seq"], about["type"])] += len(line or "") + TIE
        for entry in summaries[key]["entries"]:
            at = (key, entry["seq"], entry["type"])
            texts[at] = (texts.get(at, "") + " " + str(entry.get("text") or "")).strip()
            stamp[at] = max(stamp.get(at, 0.0), float(entry.get("ts") or 0.0))
    groups = []
    for order, group in enumerate(found["groups"]):
        items = [
            (item["key"], item["seq"], item["type"])
            for item in found["items"]
            if group["id"] in item["groups"]
        ]
        if items:
            groups.append({"id": group["id"], "items": items, "selected": order} | group)
    if not groups:
        return groups
    query = str(call.get("text") or "")
    vectors = embedder.documents([texts.get(at, "") or " " for g in groups for at in g["items"]])
    asked = embedder.queries([query])[0]
    scores = iter(np.asarray(vectors) @ np.asarray(asked))
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE docs USING fts5(body, tokenize = 'unicode61')")
    for index, group in enumerate(groups):
        group["encoder"] = max(float(next(scores)) for _ in group["items"])
        group["recent"] = max(stamp.get(at, 0.0) for at in group["items"])
        body = " ".join(texts.get(at, "") for at in group["items"])
        db.execute("INSERT INTO docs(rowid, body) VALUES (?, ?)", (index, body))
    words = {w.strip(".-_") for w in history._WORD.findall(query)}
    match = " OR ".join('"' + word.replace('"', "") + '"' for word in sorted(words) if word)
    ranked = {}
    if match:
        rows = db.execute("SELECT rowid, bm25(docs) FROM docs WHERE docs MATCH ?", (match,))
        ranked = {row: -score for row, score in rows}
    for index, group in enumerate(groups):
        group["words"] = ranked.get(index, float("-inf"))
    return _costed(groups, cost)


def _costed(groups, cost):
    for group in groups:
        group["cost"] = {at: cost[at] for at in group["items"]}
    return groups


def ordered(groups, order):
    if order == "selected":
        return sorted(groups, key=lambda g: (not g["key"], not g["bears"], g["selected"]))
    return sorted(groups, key=lambda g: (-g[order], g["selected"]))


def taken(groups, order, room):
    """The entries carried when groups are given room in an order, whole or not at all."""
    held, left = set(), room
    for group in ordered(groups, order):
        need = sum(cost for at, cost in group["cost"].items() if at not in held)
        if need <= left:
            held |= set(group["items"])
            left -= need
    return held


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("campaigns")
    parser.add_argument("--embedder", choices=("hash", "embeddinggemma"), default="hash")
    parser.add_argument("--room", type=int, nargs="+", default=[1000, 1500, 2000, 2500, 3000])
    parser.add_argument("--skip", nargs="*", default=[], help="pair ids to leave out")
    args = parser.parse_args(argv)
    campaigns = [c for c in read_jsonl(args.campaigns) if c["pair_id"] not in args.skip]
    runtime = Runtime(create(args.embedder))
    told = {"runs": 0, "facts": 0, "facts_in_a_found_group": 0, "facts_with_no_entry": 0}
    kept = {(order, room): Counter() for order in ORDERS for room in args.room}
    pairs = {(order, room): {} for order in ORDERS for room in args.room}
    for campaign in campaigns:
        kind = (campaign.get("story") or {}).get("kind", "?")
        for agent in cc.AGENTS:
            laid = cc.layout(campaign, agent)
            summaries, _ = cc.replay(laid, runtime, **cc.VARIANTS[0])
            found, call = found_groups(laid, summaries)
            groups = measured(found, call, summaries, runtime.embedder)
            wanted = labels(laid, summaries)
            everything = {at for group in groups for at in group["items"]}
            told["runs"] += 1
            told["facts"] += len(wanted)
            told["facts_with_no_entry"] += sum(at is None for at in wanted)
            told["facts_in_a_found_group"] += sum(at in everything for at in wanted)
            for (order, room), count in kept.items():
                held = taken(groups, order, room)
                got = sum(at in held for at in wanted)
                count[f"facts_{kind}"] += got
                count[f"runs_with_every_fact_{kind}"] += got == len(wanted)
                whole = pairs[(order, room)].setdefault((campaign["pair_id"], agent), True)
                pairs[(order, room)][(campaign["pair_id"], agent)] = whole and got == len(wanted)
    table = {}
    for (order, room), count in kept.items():
        row = dict(count) | {
            "pairs_with_every_fact_in_both_twins": sum(pairs[(order, room)].values())
        }
        table.setdefault(order, {})[room] = row
    told |= {"embedder": args.embedder, "made_with": runtime.identity(), "by_order_and_room": table}
    print(json.dumps(told))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
