"""Where the history arm loses an earlier fact: retention, selection or packet fit (MR180-1).

    python training/summarizer/history_diagnosis.py ROOTS.jsonl [--embedder hash] [--budget 10000]

A diagnostic, never runtime: it reads each campaign's declared facts (the answer key) to say
where the evidence a label rests on went, and nothing it reads reaches summarizer.history.
For every fact of an earlier session, in the first replay and one layout:

- kept: the reference view shows it, so the session's summary holds it; otherwise it was
  lost in retention (the summary never had it, or a damaged log took it);
- selected: summarizer.history.select() returns that fact's own entry; otherwise it was lost
  in selection;
- shown: the history view shows it; otherwise, though selected, it was lost in packet fit.

With the history view's route, whether the scorer reads it as ready (and falsely so), and
what its account says of its own completeness. Use training campaigns to debug; an
improvement is claimed only on campaigns nothing was tuned on.
"""

import argparse
import json
from collections import Counter

import campaign_arms as arms
import campaign_contract as cc
import history_builder as hb
from common import read_jsonl
from summarizer import history

STAGES = ("shown", "retention", "selection", "fit")


def _known(part, summaries):
    fields = {"key": part["id"], "summary": summaries[part["id"]]}
    fields |= {"cwd": part["cwd"], "namespace": part["namespace"]}
    return fields | {key: part[key] for key in ("alias", "agent", "session_id")}


def diagnose(campaign, runtime, budget=10000, agent="claude_code"):
    """One record for a campaign: each earlier fact with the stage where it was lost."""
    laid = cc.layout(campaign, agent)
    summaries, _ = cc.replay(laid, runtime, **cc.VARIANTS[0])
    views = arms.views_of(laid, summaries, budget)
    shown_by = cc.score(laid, summaries, views["history"], budget, arms.RULES)
    kept_by = cc.score(laid, summaries, views["reference"], budget, arms.RULES)
    blind = hb.blind(laid)
    deciding = blind["deciding"]
    others = [
        _known(part, summaries) for part in blind["parts"] if not part["later"] and not part["mine"]
    ]
    chosen = history.select(_known(deciding, summaries), deciding["laid"]["call"], others, cc.HOME)
    picked = {(item["key"], item["seq"]) for item in chosen["items"]}
    parts = {part["id"]: part for part in laid["parts"]}
    facts = []
    for fact in campaign["facts"]:
        part = parts[fact["session"]]
        if part["mine"]:
            continue
        seq = part["laid"]["where"][fact["step"]][fact["event"]]
        kept = bool(kept_by["support"].get(fact["id"]))
        selected = (part["id"], seq) in picked
        shown = bool(shown_by["support"].get(fact["id"]))
        lost = (
            "shown"
            if shown
            else "retention"
            if not kept
            else "selection"
            if not selected
            else "fit"
        )
        facts.append(
            {"fact": fact["id"], "session": fact["session"], "step": fact["step"]}
            | {"event": fact["event"], "kept": kept, "selected": selected, "shown": shown}
            | {"lost_at": lost}
        )
    account = views["history"].get("history") or {}
    story = campaign.get("story") or {}
    return {
        "campaign": campaign["id"],
        "pair": campaign["pair_id"],
        "kind": story.get("kind"),
        "route": views["history"].get("route"),
        "route_wanted": campaign.get("route"),
        "ready": bool(shown_by["ready"]),
        "false_ready": bool(shown_by["false_ready"]),
        "complete": account.get("complete"),
        "why": account.get("why", []),
        "selected_items": len(chosen["items"]),
        "packet_chars": len(views["history"]["text"]),
        "facts": facts,
    }


def summary_of(records):
    """Counts over every earlier fact and every campaign: nothing left out."""
    stages = Counter(fact["lost_at"] for record in records for fact in record["facts"])
    losing = [r for r in records if any(f["lost_at"] != "shown" for f in r["facts"])]
    return {
        "campaigns": len(records),
        "earlier_facts": sum(len(r["facts"]) for r in records),
        "earlier_facts_by_stage": {stage: stages.get(stage, 0) for stage in STAGES},
        "campaigns_losing_a_fact": len(losing),
        "of_them_ready": sum(r["ready"] for r in losing),
        "of_them_account_complete": sum(r["complete"] is True for r in losing),
        "of_them_route": dict(Counter(str(r["route"]) for r in losing)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("roots")
    parser.add_argument("--embedder", default="embeddinggemma", choices=("embeddinggemma", "hash"))
    parser.add_argument("--budget", type=int, default=10000)
    parser.add_argument("--agent", default="claude_code", choices=cc.AGENTS)
    parser.add_argument("--out", help="write one record per campaign here (JSON Lines)")
    args = parser.parse_args(argv)
    from summarizer.embedder import create
    from summarizer.summarize import Runtime

    runtime = Runtime(create(args.embedder))
    records = [diagnose(c, runtime, args.budget, args.agent) for c in read_jsonl(args.roots)]
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
    told = summary_of(records) | {"embedder": args.embedder, "budget": args.budget}
    told |= {"agent": args.agent, "made_with": runtime.identity()}
    print(json.dumps(told, sort_keys=True))


if __name__ == "__main__":
    main()
