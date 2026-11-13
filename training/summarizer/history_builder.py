"""A packet builder that uses summarizer.history, measured like the two baselines.

    python training/summarizer/history_builder.py training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl

campaign_contract.py has two builders that show the gap: today's packet, and the newest
lines of every session. This is the third: today's packet with what summarizer.history
selects from the other sessions, written in the section the contract gives it. It is run
on the same campaigns, through the same four replays, and scored by the same scorer.

The builder is given only what a server would have: for each session its agent, id, working
directory, namespace, a display alias and whether it had happened, and the id and hash of
the call under decision. It never reads a campaign's facts, bridges, limits, story, route or
expectation, nor any step. blind() makes a layout with all of that removed, and main() runs
the builder on that; a builder that needs the answer key cannot run.

Room is given to whole groups of evidence (summarizer.history says which items make one):
a group whose lines do not all fit is left out whole, and so is one with a line that cannot
be shown. When that group is key evidence the account says so and the route is undecided,
also when nothing of the other sessions is shown at all. All of that is the runtime's:
summarizer.history.packet() builds the packet, and this file lays the campaign out for it
and has the result scored.
"""

import argparse
import json

import campaign_contract as cc
from common import read_jsonl
from summarizer import history

KEPT = ("id", "agent", "session_id", "cwd", "namespace", "alias", "later", "mine")


def blind(laid):
    """The layout as a server would know it: identities and the call, and nothing else."""
    parts = []
    for part in laid["parts"]:
        mine = {key: part[key] for key in KEPT}
        mine["laid"] = {"call": part["laid"]["call"], "identity": part["laid"]["identity"]}
        parts.append(mine)
    deciding = next(part for part in parts if part["mine"])
    return {"agent": laid["agent"], "parts": parts, "deciding": deciding}


def build(laid, summaries, budget=3000):
    """Today's packet, with the section for other sessions that summarizer.history selects.

    The packet is built by the runtime's own summarizer.history.packet(): what is measured
    here is the code a server would run, given what a server would have.
    """

    def known(part):
        fields = {"key": part["id"], "summary": summaries[part["id"]]}
        fields |= {"cwd": part["cwd"], "namespace": part["namespace"]}
        return fields | {key: part[key] for key in ("alias", "agent", "session_id")}

    deciding = laid["deciding"]
    others = [known(part) for part in laid["parts"] if not part["later"] and not part["mine"]]
    call, identity = deciding["laid"]["call"], deciding["laid"]["identity"]
    return history.packet(known(deciding), call, others, cc.HOME, budget, identity)


def evaluate(campaigns, runtime, budget=3000, agents=cc.AGENTS, rules=9):
    """One row per campaign and agent: this builder, run without the answer key, scored with
    it under one version of the contract, over all four replays."""
    rows = []
    for campaign in campaigns:
        for agent in agents:
            laid = cc.layout(campaign, agent)
            replays = [cc.replay(laid, runtime, **variant) for variant in cc.VARIANTS]
            views = [build(blind(laid), summaries, budget) for summaries, _ in replays]
            scores = [
                cc.score(laid, replay[0], view, budget, rules)
                for replay, view in zip(replays, views, strict=True)
            ]
            row = {"id": campaign["id"], "family": campaign["family"], "agent": agent}
            row |= {"expect": campaign["expect"], "builder": "history"} | scores[0]
            row["first_replay_passes"] = scores[0]["passes"]
            row["passes"] = all(one["passes"] for one in scores)
            told = [cc.stable(cc._told(view)) for view in views]
            row["variants_agree"] = all(one == told[0] for one in told)
            row["variants_agree_but_for_named_numbers"] = all(
                cc.exact(one) == cc.exact(told[0]) for one in told
            )
            row["returns"] = [went.get("returns", 0) for _, went in replays]
            rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("campaigns", help="a JSON Lines file of campaigns")
    parser.add_argument("--budget", type=int, default=3000)
    parser.add_argument("--embedder", choices=("hash", "embeddinggemma"), default="hash")
    parser.add_argument("--rows", action="store_true", help="print a line for every run as well")
    parser.add_argument(
        "--scorer", type=int, choices=tuple(cc.SCORERS), default=9, help="version of the contract"
    )
    args = parser.parse_args(argv)
    from summarizer.embedder import create
    from summarizer.summarize import Runtime

    campaigns = read_jsonl(args.campaigns)
    bad = [c.get("id", "?") for c in campaigns if cc.validate(c, args.scorer)]
    if bad:
        print(json.dumps({"problems": len(bad), "ids": bad[:10]}))
        return 1
    rows = evaluate(campaigns, Runtime(create(args.embedder)), args.budget, rules=args.scorer)
    for row in rows if args.rows else ():
        print(json.dumps(row))
    for name, counts in cc.report(rows).items():
        print(json.dumps({"builder": name, "budget": args.budget} | counts))
    told = {"campaigns": len(campaigns), "runs": len(rows), "scorer": cc.SCORERS[args.scorer]}
    print(json.dumps(told | {"code": cc.code_version()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
