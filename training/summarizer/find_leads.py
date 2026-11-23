"""Leads from outside what the exact ties find, measured on every run at equal numbers.

    python training/summarizer/find_leads.py CAMPAIGNS [--embedder hash|embeddinggemma]
        [--k 1 3 5 10] [--skip PAIR ...] [--rows OUT.jsonl]

A diagnostic: it changes no packet and decides nothing. For every run (a campaign laid out
for one agent, the first replay), the candidates are the entries of the earlier sessions
that no group found by summarizer.history.select() holds. They are ranked against the
proposed call, as the server has it before it runs, in two orders: full-text (SQLite FTS5,
bm25, on the call's words) and the encoder's similarity (the call as a query, each entry
as a document). At each k (an equal maximum number of candidates: full-text can return
fewer) the table counts how many named facts the first k candidates of each order reach, the same for an interleaving of the two that takes k in all, and what a
random draw of k from the same pool reaches on average (k/N for each fact in a pool of N).

Every run is measured; no label decides whether it is (MR156). A candidate is one entry
as its summary holds it: a result that came in several pieces is several candidates, each
known by its event, its kind and its own digest. A named fact is located by the scorer's
own locate(), by identity; one that no entry holds, and one already inside a found group,
are counted apart. Candidates added on runs where nothing was missed are counted too:
they are what a finder costs. Costs are the characters of each candidate's own line plus
an allowance for the words of a tie; no packet is built here.

Counts are printed. --rows writes one record per run with its pool's size and the ranks
of its named facts under each order, for inspection: identities and numbers, never text.
"""

import argparse
import json
import sqlite3
import statistics
from collections import Counter

import numpy as np

import campaign_contract as cc
import case_contract as contract
from common import read_jsonl
from rank_groups import found_groups
from summarizer import history
from summarizer.embedder import create
from summarizer.summarize import Runtime
from summarizer.text import entry_lines

ORDERS = ("words", "encoder", "both")
TIE = 50  # characters allowed for an alias and the words of a tie on each line


def ident(key, entry):
    """One entry of a summary: its session, event, kind and own identity."""
    own = entry.get("sha256") or entry.get("call_id") or ""
    return (key, entry["seq"], entry["type"], own)


def named(laid, summaries):
    """[(fact id, the entry that holds it, or None)] for the named facts of earlier sessions."""
    parts = {part["id"]: part for part in laid["parts"]}
    found = []
    for fact in laid["campaign"]["facts"]:
        part = parts[fact["session"]]
        if part["mine"]:
            continue
        mine = next(f for f in part["laid"]["case"]["facts"] if f["id"] == fact["id"])
        for member in contract.members(mine):
            entry, _ = contract.locate(part["laid"], summaries[part["id"]], member)
            found.append((fact["id"], ident(part["id"], entry) if entry else None))
    return found


def pool_of(found, laid, summaries):
    """{entry identity: (text, cost)} for every entry no found group holds."""
    inside = {(item["key"], item["seq"], item["type"]) for item in found["items"]}
    pool = {}
    for part in laid["parts"]:
        if part["later"] or part["mine"]:
            continue
        key, summary = part["id"], summaries[part["id"]]
        costs = {}
        for about, line in entry_lines(summary):
            costs[ident(key, about)] = len(line or "") + TIE
        for entry in summary["entries"]:
            at = ident(key, entry)
            text = str(entry.get("text") or "").strip()
            if (key, entry["seq"], entry["type"]) not in inside and text and at in costs:
                pool[at] = (text, costs[at])
    return pool


def ranked(pool, call, embedder):
    """{"words": [identities, best first], "encoder": [...], "both": [...]}."""
    ats = sorted(pool)
    if not ats:
        return {order: [] for order in ORDERS}
    query = str(call.get("text") or "")
    scores = np.asarray(embedder.documents([pool[at][0] for at in ats])) @ np.asarray(
        embedder.queries([query])[0]
    )
    by_encoder = [ats[i] for i in np.argsort(-scores, kind="stable")]
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE docs USING fts5(body, tokenize = 'unicode61')")
    db.executemany(
        "INSERT INTO docs(rowid, body) VALUES (?, ?)", enumerate(pool[at][0] for at in ats)
    )
    words = sorted({w.strip(".-_") for w in history._WORD.findall(query)} - {""})
    match = " OR ".join('"' + word.replace('"', "") + '"' for word in words)
    by_words = []
    if match:
        rows = db.execute(
            "SELECT rowid FROM docs WHERE docs MATCH ? ORDER BY bm25(docs), rowid", (match,)
        )
        by_words = [ats[row] for (row,) in rows]
    both, seen = [], set()
    for pair in zip(by_words + [None] * len(by_encoder), by_encoder, strict=False):
        for at in pair:
            if at is not None and at not in seen:
                seen.add(at)
                both.append(at)
    return {"words": by_words, "encoder": by_encoder, "both": both}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("campaigns")
    parser.add_argument("--embedder", choices=("hash", "embeddinggemma"), default="hash")
    parser.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10])
    parser.add_argument("--skip", nargs="*", default=[], help="pair ids to leave out")
    parser.add_argument("--rows", help="write one record per run here: identities and numbers")
    args = parser.parse_args(argv)
    campaigns = [c for c in read_jsonl(args.campaigns) if c["pair_id"] not in args.skip]
    runtime = Runtime(create(args.embedder))
    told = Counter()
    hits = {(order, k): Counter() for order in (*ORDERS, "random") for k in args.k}
    added = {(order, k): Counter() for order in ORDERS for k in args.k}
    pools, missed_rows, rows = [], [], []
    for campaign in campaigns:
        kind = (campaign.get("story") or {}).get("kind", "?")
        for agent in cc.AGENTS:
            laid = cc.layout(campaign, agent)
            summaries, _ = cc.replay(laid, runtime, **cc.VARIANTS[0])
            found, call = found_groups(laid, summaries)
            pool = pool_of(found, laid, summaries)
            orders = ranked(pool, call, runtime.embedder)
            facts = named(laid, summaries)
            inside = {(item["key"], item["seq"], item["type"]) for item in found["items"]}
            told["runs"] += 1
            pools.append(len(pool))
            record = {"run": f"{campaign['id']}/{agent}", "pair": campaign["pair_id"]}
            record |= {"kind": kind, "pool": len(pool), "facts": []}
            for fact, at in facts:
                told["facts"] += 1
                if at is None:
                    told["facts_with_no_entry"] += 1
                    continue
                if at[:3] in inside:
                    told["facts_in_a_found_group"] += 1
                    continue
                if at not in pool:
                    told["facts_outside_groups_and_pool"] += 1
                    continue
                told[f"facts_in_the_pool_{kind}"] += 1
                place = {o: orders[o].index(at) + 1 if at in orders[o] else None for o in ORDERS}
                record["facts"].append({"fact": fact, "rank": place})
                missed_rows.append((campaign["pair_id"], fact, agent, len(pool), place))
                for k in args.k:
                    for order in ORDERS:
                        hits[(order, k)][kind] += bool(place[order] and place[order] <= k)
                    hits[("random", k)][kind] += min(k, len(pool)) / len(pool)
            for k in args.k:
                for order in ORDERS:
                    taken = orders[order][:k]
                    added[(order, k)]["candidates"] += len(taken)
                    added[(order, k)]["chars"] += sum(pool[at][1] for at in taken)
            rows.append(record)
    sizes = sorted(pools)
    table = {}
    for k in args.k:
        row = {}
        for order in (*ORDERS, "random"):
            got = hits[(order, k)]
            row[order] = round(sum(got.values()), 2)
            row[f"{order}_attack_benign"] = [round(got["attack"], 2), round(got["benign"], 2)]
        for order in ORDERS:
            row[f"{order}_candidates_added"] = added[(order, k)]["candidates"]
            row[f"{order}_chars_added"] = added[(order, k)]["chars"]
        table[k] = row
    missed = sorted(missed_rows)
    by_fact = Counter((pair, fact) for pair, fact, *_ in missed)
    result = dict(told) | {
        "pool_median": statistics.median(sizes) if sizes else 0,
        "pool_max": sizes[-1] if sizes else 0,
        "unit": "an entry of a summary, by session, event, kind and its own digest",
        "by_k": table,
        "facts_in_the_pool_by_pair": {f"{pair}/{fact}": n for (pair, fact), n in by_fact.items()},
        "embedder": args.embedder,
        "made_with": runtime.identity(),
    }
    if args.rows:
        with open(args.rows, "w", encoding="utf-8") as out:
            for record in rows:
                out.write(json.dumps(record) + "\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
