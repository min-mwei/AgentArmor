"""Replays a control set that check_controls.py accepts, and says of each view what its packet does
beside what the control expects (data request 012, MR199-3, MR200-2).

A view is a control root as the replay delivers it to one deciding agent (check_controls.views).
Each is replayed through the real hook and daemon, its history packet built blind, and scored by
the campaign contract. There are three outcomes, kept apart:

- "complete diagnostic passed": a view expected to give a complete packet, which the contract's
  scorer passes (complete, ready, its evidence shown, its route the campaign's).
- "expected negative held": a view expected to give an incomplete packet for the reasons its
  control names (incomplete_because). It holds only when its incompleteness is exactly what is
  permitted and every other piece of evidence survives: it is not ready; every named reason is
  present, and no reason is present beyond those named and what they entail (a precise reason of
  the lookup entails "lookup_unfinished"); every fact the replay leaves available is shown, with
  its producer where it has one, except those the control says its reason may take
  (control.may_lose); every bridge of the campaign is shown, unless a capture gap is
  permitted; no named file the replay shows touched lacks its account; nothing is false,
  misplaced or leaked; and the packet's route is undecided for a history reason, and otherwise
  the campaign's (an unresolved call of the call's own session alone leaves it asked, not
  ready: that authorizes nothing).
- "regression": anything else.

Each row keeps the scorer's full diagnostics beside its outcome, and three routes apart: the
authored campaign's route, the packet's route, and the expectation's packet field. The expected
decision is the authors' label for a future reader: nothing here asks a reader or a model, and no
host action is measured.

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
# What a reason for an incomplete packet brings with it: a precise reason of the lookup leaves the
# lookup unfinished, and says so as "lookup_unfinished" too. Nothing else is permitted with it.
ENTAILS = {reason: {"lookup_unfinished"} for reason in UNSUPPORTED}
PASSED, HELD, REGRESSION = "complete diagnostic passed", "expected negative held", "regression"


def runtime_for(embedder):
    """The summarizer runtime a review replays with, made as campaign_arms.py makes it: the CPU
    stand-in, or the pinned encoder with the built-in probes."""
    from summarizer.embedder import create
    from summarizer.summarize import Runtime

    return Runtime(create(embedder))


def replay_view(root, layout, runtime, budget):
    """(laid, summaries, view, what the daemon went through) for one view."""
    laid = campaigns.layout(root, layout)
    summaries, went = campaigns.replay(laid, runtime)
    return laid, summaries, builder.build(builder.blind(laid), summaries, budget), went


def reasons_of(view):
    """Every reason the packet gives for being incomplete: why its history is unfinished, what
    the lookup could not tell, and an unresolved call of the call's own session."""
    history = view.get("history") or {}
    present = set(history.get("why") or ()) | set(history.get("unsupported") or ())
    return present | ({"unresolved_condition"} if view.get("unresolved_conditions") else set())


def held_problems(score, view, because, may_lose=()):
    """Why an expected negative does not hold: an empty list when it does (see the docstring).
    may_lose names the facts the permitted reason may take (control.may_lose)."""
    because, present = set(because), reasons_of(view)
    allowed = because | {more for reason in because for more in ENTAILS.get(reason, ())}
    problems = []
    if score["ready"]:
        problems.append("ready, though expected incomplete")
    problems += [f"named reason absent: {reason}" for reason in sorted(because - present)]
    problems += [f"reason not permitted: {reason}" for reason in sorted(present - allowed)]
    lost = [
        name
        for name, shown in score["support"].items()
        if score["available"][name] and not shown and name not in may_lose
    ]
    problems += [f"available fact not shown: {name}" for name in sorted(lost)]
    if score["chains"][0] != score["chains"][1] and "capture_gap" not in because:
        problems.append("a bridge of the campaign not shown")
    for key in ("objects_missing", "forged", "misplaced", "leaked"):
        if score.get(key):
            problems.append(f"{key}: {score[key]}")
    routed = "undecided" if because & {*HISTORY_REASONS, *UNSUPPORTED} else score["route_wanted"]
    if view.get("route") != routed:
        problems.append(f"route {view.get('route')}, where {routed} is expected")
    return problems


def verdict_of(root, agent, layout, expected, because, laid, summaries, view, budget):
    """One view's row: what its packet does beside what its control expects, and the outcome."""
    score = campaigns.score(laid, summaries, view, budget, SCORER)
    because = because or []
    if expected["packet"] == "complete":
        problems = [] if score["passes"] else ["the complete-packet scorer does not pass it"]
        verdict = PASSED if not problems else REGRESSION
    else:
        problems = held_problems(score, view, because, root["control"].get("may_lose") or ())
        verdict = HELD if not problems else REGRESSION
    present = reasons_of(view)
    accounts = (view.get("history") or {}).get("objects") or []
    return {
        "root": root["id"],
        "row": root["control"]["row"],
        "kind": (root.get("story") or {}).get("kind"),
        "agent": agent,
        "layout": layout,
        "expected": expected,
        "incomplete_because": because,
        "packet": "incomplete" if present else "complete",
        "reasons": sorted(present),
        "campaign_route": score["route_wanted"],
        "packet_route": view.get("route"),
        "verdict": verdict,
        "problems": problems,
        "score": {key: value for key, value in score.items() if key != "scorer"},
        "objects": [builder_line(account, laid) for account in accounts],
        "packet_chars": len(view["text"]),
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
            laid, summaries, view, _ = replay_view(root, seen["layout"], runtime, budget)
            found.append(
                verdict_of(
                    root,
                    agent,
                    seen["layout"],
                    seen["expected"],
                    seen["incomplete_because"],
                    laid,
                    summaries,
                    view,
                    budget,
                )
            )
    verdicts = Counter(row["verdict"] for row in found)
    summary = {
        "roots": coverage["roots"],
        "views": len(found),
        "both_clients": coverage["both_clients"],
        "one_client": coverage["one_client"],
        "complete_diagnostic_passed": verdicts[PASSED],
        "expected_negative_held": verdicts[HELD],
        "regression": verdicts[REGRESSION],
        "false_ready": sum(row["score"]["false_ready"] for row in found),
        "forged": sum(row["score"]["forged"] for row in found),
        "budget": budget,
        "scorer": SCORER,
    }
    return found, summary


def markdown(found, summary, title):
    lines = [f"# {title}", "", "```json", json.dumps(summary, indent=1), "```", ""]
    lines += [
        "| Row | Root (kind) | Agent | Expected decision | Expected packet (why) "
        "| Packet (reasons) | Campaign route | Packet route | Outcome |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in found:
        why = f" ({', '.join(row['incomplete_because'])})" if row["incomplete_because"] else ""
        reasons = f" ({', '.join(row['reasons'])})" if row["reasons"] else ""
        verdict = row["verdict"]
        if verdict == REGRESSION:
            verdict = f"**regression**: {'; '.join(row['problems'])}"
        lines.append(
            f"| {row['row']} | {row['root']} ({row['kind']}) | {row['agent']} "
            f"| {row['expected']['decision']} | {row['expected']['packet']}{why} "
            f"| {row['packet']}{reasons} | {row['campaign_route']} | {row['packet_route']} "
            f"| {verdict} |"
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
    runtime = runtime_for(args.embedder)
    found, summary = review(rows, runtime, args.budget)
    summary |= {"embedder": args.embedder, "made_with": runtime.identity()}
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
