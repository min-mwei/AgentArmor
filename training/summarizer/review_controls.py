"""Replays a control set that check_controls.py accepts, and says of each view what its packet does
beside what the control expects (data request 012, MR199-3).

A view is a control root as the replay delivers it to one deciding agent (check_controls.views).
Each is replayed through the real hook and daemon, its history packet built blind, and scored by
the campaign contract. A view expected to give a complete packet passes when the packet is
complete and the contract's scorer passes it. A view expected to give an incomplete packet is an
expected negative: it holds when the packet says it is incomplete for every reason the control
names (control.incomplete_because), routes the call undecided, is not ready, and says nothing the
replay does not bear out. Anything else is a regression, counted apart from an expected negative
that held. The expected decision is the authors' label for a future reader: nothing here asks a
reader or a model, so nothing here measures one.

    python review_controls.py data/controls/session-controls-001-r1.jsonl --out review.jsonl \\
        [--markdown review.md] [--embedder hash|embeddinggemma] [--budget 11000]
"""

import argparse
import json
import sys
from collections import Counter

import campaign_contract as campaigns
import check_controls
import history_builder as builder
from check_controls import HISTORY_REASONS, UNSUPPORTED
from common import read_jsonl

SCORER = 16


def runtime_for(embedder):
    """The summarizer runtime a review replays with: the CPU stand-in, or the pinned encoder."""
    from summarizer.embedder import HashEmbedder
    from summarizer.summarize import Runtime, load

    if embedder == "hash":
        return Runtime(HashEmbedder())
    return load()


def review_view(root, agent, layout, expected, because, runtime, budget):
    """One view's row: what its packet does beside what its control expects, and the verdict."""
    laid = campaigns.layout(root, layout)
    summaries, went = campaigns.replay(laid, runtime)
    view = builder.build(builder.blind(laid), summaries, budget)
    score = campaigns.score(laid, summaries, view, budget, SCORER)
    history = view.get("history") or {}
    complete = history.get("complete", True)
    why = sorted(history.get("why") or [])
    because = because or []
    present = set(why) | set(history.get("unsupported") or ())
    present |= {"unresolved_condition"} if view.get("unresolved_conditions") else set()
    if expected["packet"] == "complete":
        verdict = "pass" if complete and score["passes"] else "regression"
    else:
        # An unfinished history routes the call undecided; an unresolved call alone keeps the
        # campaign's route.
        unfinished = set(because) & {*HISTORY_REASONS, *UNSUPPORTED}
        routed = "undecided" if unfinished else score["route_wanted"]
        held = (
            not score["ready"]
            and set(because) <= present
            and view.get("route") == routed
            and score["forged"] == 0
        )
        verdict = "expected negative held" if held else "regression"
    accounts = history.get("objects") or []
    return {
        "root": root["id"],
        "row": root["control"]["row"],
        "kind": (root.get("story") or {}).get("kind"),
        "agent": agent,
        "layout": layout,
        "expected": expected,
        "incomplete_because": because,
        "packet": "complete" if complete and not present else "incomplete",
        "why": sorted(present),
        "route": view.get("route"),
        "route_wanted": score["route_wanted"],
        "ready": score["ready"],
        "false_ready": score["false_ready"],
        "forged": score["forged"],
        "objects_missing": score.get("objects_missing", 0),
        "passes": score["passes"],
        "verdict": verdict,
        "objects": [builder_line(account, laid) for account in accounts],
        "packet_chars": len(view["text"]),
        "rebuilt": went.get("rebuilt", 0) if isinstance(went, dict) else 0,
    }


def builder_line(account, laid):
    """The object line the packet shows for an account."""
    from summarizer.history import object_line

    return object_line(account, laid["deciding"]["cwd"])


def review(rows, runtime, budget):
    """(view rows, summary) for a control file check_controls.py accepts."""
    problems, reports, coverage = check_controls.check(rows)
    if problems:
        raise SystemExit("the control set does not pass check_controls.py:\n" + "\n".join(problems))
    roots = {root["id"]: root for root in rows}
    found = []
    for report in reports:
        root = roots[report["id"]]
        for agent, seen in sorted(report["views"].items()):
            because = seen["incomplete_because"]
            found.append(
                review_view(root, agent, seen["layout"], seen["expected"], because, runtime, budget)
            )
    verdicts = Counter(row["verdict"] for row in found)
    summary = {
        "roots": coverage["roots"],
        "views": len(found),
        "both_clients": coverage["both_clients"],
        "one_client": coverage["one_client"],
        "pass": verdicts["pass"],
        "expected_negative_held": verdicts["expected negative held"],
        "regression": verdicts["regression"],
        "false_ready": sum(row["false_ready"] for row in found),
        "forged": sum(row["forged"] for row in found),
        "budget": budget,
        "scorer": SCORER,
    }
    return found, summary


def markdown(found, summary, title):
    lines = [f"# {title}", "", "```json", json.dumps(summary, indent=1), "```", ""]
    lines += [
        "| Row | Root (kind) | Agent | Expected decision | Expected packet | Packet (why) "
        "| Route (campaign) | Verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in found:
        why = f" ({', '.join(row['why'])})" if row["why"] else ""
        verdict = row["verdict"] if row["verdict"] != "regression" else "**regression**"
        lines.append(
            f"| {row['row']} | {row['root']} ({row['kind']}) | {row['agent']} "
            f"| {row['expected']['decision']} | {row['expected']['packet']} "
            f"| {row['packet']}{why} | {row['route']} ({row['route_wanted']}) | {verdict} |"
        )
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("controls")
    parser.add_argument("--out", required=True, help="one JSON line per view")
    parser.add_argument("--markdown", help="a table of every view, for review")
    parser.add_argument("--embedder", choices=("hash", "embeddinggemma"), default="hash")
    parser.add_argument("--budget", type=int, default=11000)
    args = parser.parse_args(argv)
    rows = list(read_jsonl(args.controls))
    found, summary = review(rows, runtime_for(args.embedder), args.budget)
    summary["embedder"] = args.embedder
    with open(args.out, "w", encoding="utf-8") as out:
        for row in found:
            out.write(json.dumps(row, sort_keys=True) + "\n")
    if args.markdown:
        title = f"Control review of {args.controls.rsplit('/', 1)[-1]}"
        with open(args.markdown, "w", encoding="utf-8") as out:
            out.write(markdown(found, summary, title))
    print(json.dumps(summary))
    return 1 if summary["regression"] else 0


if __name__ == "__main__":
    sys.exit(main())
