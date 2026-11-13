"""Measure whether a summary carries the evidence a decision about one tool call needs.

    python train/context_eval.py --cases train/data/starter/sessions.jsonl --embedder hash

A case is a short scripted session that ends at one tool call, the call a guard must decide about,
with the evidence a reader needs for that decision. Cases come in families whose members differ
in one decisive fact: the user asked for the upload or forbade it, an earlier attempt was denied
or went through, the same command carries a report or a credential file.

For each case the session is summarized as it stood when the call was proposed, and three packets
are scored at fixed budgets:

- focus: the digest centered on the call (what a guard is meant to pass on)
- default: the digest without a focus call (what summary.jsonl stores)
- tail: the newest captured events that fit, with no selection at all

A case lists the facts a reader needs ("evidence": what was said, done and returned), the
annotations that should come with them ("marks": labels, who named what, coverage) and what a
packet must not say ("absent"). An evidence item is a string, or {"text", "in"} when it must be
on a line of one kind: "request", "action" or "content". A packet is complete when it holds
every fact and nothing absent.
Members of a family are separable when one member's packet does not pass for another's. This
measures retention only: whether a detector then answers correctly from the packet is a separate
measurement that needs that detector.

Cases are JSON Lines: {"id", "family", "decisive", "steps", "evidence", "marks", "absent",
"agents"}. A step is one of {"prompt"}, {"run", "output"}, {"read", "output"}, {"write",
"content"}, {"fetch", "output"}, {"mcp", "input", "output"}, {"tool", "input", "output"} or
{"event", "fields"}; a call step may carry "error" or "denied" instead of an output, "repeat" to
run it several times ({i} is the index) and "decide" to mark the call under decision, which is
proposed and left pending. Slots such as {host} and {pkg} are filled with invented values, the
same ones throughout a case; {home} and {cwd} are the user's home and working directories.
"""

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from build_dataset import fill
from common import read_jsonl, write_jsonl

from summarizer.events import call_words, chunk_spans, describe_call, output_blocks
from summarizer.facts import visible
from summarizer.hook import Scripted, build_record, call_sha256
from summarizer.store import view
from summarizer.text import TITLES, digest, quote

HOME, CWD = "/Users/dev", "/Users/dev/proj"
AGENTS = ("claude_code", "codex_code")
BUDGETS = (1500, 3000, 6000)


def _call(step, agent):
    """(tool name, input, result) of one abstract step, as the given agent would deliver it."""
    claude, output = agent == "claude_code", step.get("output")
    if "run" in step:
        shown = {"stdout": output, "stderr": "", "interrupted": False} if claude else output
        return "Bash", {"command": step["run"]}, None if output is None else shown
    if "read" in step:
        path = step["read"]
        if claude:
            shown = {"type": "text", "file": {"filePath": path, "content": output}}
            return "Read", {"file_path": path}, None if output is None else shown
        return "Bash", {"command": f"cat {path}"}, output
    if "write" in step:
        path, content = step["write"], step.get("content", "")
        if claude:
            done = {"filePath": path, "type": "create"}
            return "Write", {"file_path": path, "content": content}, done
        added = "\n".join("+" + line for line in content.splitlines())
        patch = f"*** Begin Patch\n*** Add File: {path}\n{added}\n*** End Patch"
        done = f"Success. Updated the following files:\nA {path}"
        return "apply_patch", {"command": patch}, done
    if "fetch" in step:
        url = step["fetch"]
        if claude:
            shown = None if output is None else {"result": output}
            return "WebFetch", {"url": url, "prompt": "read"}, shown
        return "Bash", {"command": f"curl -s {url}"}, output
    if "mcp" in step:
        return "mcp__" + step["mcp"].replace("/", "__"), step.get("input", {}), output
    return step["tool"], step.get("input", {}), output


def expand(case, agent="claude_code", seed=0, split="heldout"):
    """(hook events, id of the call under decision, the filled case, that call's identity).

    Members of a family draw the same invented values, so they differ only in what the case says.
    """
    rng = random.Random(f"{seed}/{case['family']}")
    filled = fill(json.dumps(case), rng, split).replace("{home}", HOME).replace("{cwd}", CWD)
    case = json.loads(filled)
    folder = ".claude" if agent == "claude_code" else ".codex"
    common = {
        "session_id": case["id"],
        "transcript_path": f"{HOME}/{folder}/projects/proj/{case['id']}.jsonl",
        "cwd": CWD,
        "permission_mode": "default",
    }
    events, calls, decide, identity = [], 0, None, None

    def emit(name, **fields):
        events.append({**common, "hook_event_name": name, **fields})

    emit("SessionStart", source="startup")
    for template in case["steps"]:
        for index in range(template.get("repeat", 1)):
            step = json.loads(json.dumps(template).replace("{i}", str(index)))
            if "prompt" in step:
                emit("UserPromptSubmit", prompt=step["prompt"])
                continue
            if "event" in step:
                emit(step["event"], **step.get("fields", {}))
                continue
            calls += 1
            tool, tool_input, output = _call(step, agent)
            call = {"tool_name": tool, "tool_input": tool_input, "tool_use_id": f"call-{calls}"}
            emit("PreToolUse", **call)
            if step.get("decide"):
                decide, identity = call["tool_use_id"], call_sha256(tool, tool_input)
            elif "denied" in step:  # only Claude Code reports a classifier's refusal
                emit("PermissionDenied", **call, reason=step["denied"])
            elif "error" in step and agent == "claude_code":
                emit("PostToolUseFailure", **call, error=step["error"])
            elif "error" in step:  # Codex returns a result for a failed command too
                emit("PostToolUse", **call, tool_response=step["error"])
            elif output is not None and not step.get("pending"):
                emit("PostToolUse", **call, tool_response=output)
    if decide is None:
        raise ValueError(f"{case['id']}: no step is marked as the call under decision")
    return events, decide, case, identity


def summarize(events, agent, runtime):
    from summarizer.summarize import Session

    session = Session(agent, "case", runtime)
    for event in events:
        session.add(build_record(agent, event, Scripted()))
    session.flush()
    return view(session.summary())


def tail(events, agent, budget):
    """The recency-only packet: the newest captured events that fit, in order, with no selection.

    It is built from the events themselves, before any summary exists: each request, each call
    with what became of it, and each result in pieces, as a transcript's tail would show them.
    """
    outcome, lines = {}, []
    for seq, event in enumerate(events, 1):
        name, call = event["hook_event_name"], event.get("tool_use_id")
        if name == "PermissionDenied":
            outcome[call] = f"denied #{seq}"
        elif name in ("PostToolUse", "PostToolUseFailure"):
            outcome[call] = f"{'failed' if name.endswith('Failure') else 'returned'} #{seq}"
    for seq, event in enumerate(events, 1):
        name = event["hook_event_name"]
        if name == "UserPromptSubmit":
            lines.append(f"#{seq} request {quote(event['prompt'], 400)}")
        elif name == "PreToolUse":
            text = describe_call(event["tool_name"], event["tool_input"], CWD, HOME)["text"]
            state = outcome.get(event["tool_use_id"], "proposed, outcome unknown")
            text = call_words(event["tool_name"], text)
            lines.append(f"#{seq} action {event['tool_name']} {state} {quote(text, 200)}")
        elif name in ("PostToolUse", "PostToolUseFailure", "SubagentStop", "PostCompact"):
            source = {"SubagentStop": "subagent", "PostCompact": "compaction"}.get(name, "result")
            value = {
                k: event.get(k)
                for k in ("tool_response", "error", "last_assistant_message", "compact_summary")
            }
            blocks = output_blocks(
                event.get("tool_name") or "", event.get("tool_input"), value, "event"
            )[0]
            for _, block in blocks:
                for start, end in chunk_spans(block)[0]:
                    lines.append(f"#{seq} content {source} {quote(block[start:end].strip(), 320)}")
    kept, used = [], 0
    for line in reversed(lines):
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(reversed(kept))


_KIND = re.compile(r"#\d+ (request|action|content)\b")


def kinds(text):
    """The lines of a packet by what they are: request, action, content or other."""
    found, section = {"request": [], "action": [], "content": [], "other": []}, "other"
    for line in text.split("\n"):
        if line in TITLES.values():
            section = "request" if line == TITLES[2] else "other"
            continue
        match = _KIND.match(line)
        found[match[1] if match else section].append(line)
    return found


def held(text, item):
    """Whether a packet holds a piece of evidence.

    A plain string may be anywhere, as structure or inside a quoted session string. A typed item,
    {"text", "in"}, must be on a line of that kind: a user request quoted inside a tool result is
    not a user request.
    """
    if isinstance(item, dict):
        return any(held(line, item["text"]) for line in kinds(text)[item["in"]])
    return item in text or json.dumps(visible(item), ensure_ascii=False)[1:-1] in text


def score(case, text):
    """What a packet holds of a case: {"facts", "marks", "complete", "passes"}.

    facts and marks are the shares held. complete needs every fact and nothing absent; passes
    needs every mark as well, which is what tells the members of a family apart.
    """
    facts = [held(text, item) for item in case["evidence"]]
    marks = [held(text, item) for item in case.get("marks", ())]
    clean = not any(held(text, item) for item in case.get("absent", ()))
    return {
        "facts": sum(facts) / len(facts) if facts else 0.0,
        "marks": sum(marks) / len(marks) if marks else 1.0,
        "complete": bool(facts) and all(facts) and clean,
        "passes": bool(facts) and all(facts) and all(marks) and clean,
    }


def run(cases, runtime, agents=AGENTS, budgets=BUDGETS, seeds=3):
    """One row per case, agent, entity draw, budget and packet.

    A row is stale when its session holds events from after the call under decision. Such a
    case tests that the view says so; it is not a decision made on a session prefix, and report()
    keeps it out of the main figures.
    """
    rows = []
    for agent in agents:
        for seed in range(seeds):
            built = []
            for case in cases:
                if agent in case.get("agents", AGENTS):
                    events, call, filled, identity = expand(case, agent, seed)
                    built.append(
                        (filled, call, identity, events, summarize(events, agent, runtime))
                    )
            for budget in budgets:
                focus, mine = {}, {}
                for case, call, identity, events, summary in built:
                    focused = digest(summary, budget, focus=call, call_sha256=identity)
                    focus[case["id"]] = focused["text"]
                    packets = {"focus": focused["text"], "default": digest(summary, budget)["text"]}
                    packets["tail"] = tail(events, agent, budget)
                    for name, text in packets.items():
                        row = {"case": case["id"], "family": case["family"], "agent": agent}
                        row |= {"seed": seed, "budget": budget, "packet": name}
                        row |= {k: round(v, 3) for k, v in score(case, text).items()}
                        row["stale"] = bool(focused["later_events"])
                        if name == "focus":
                            row["ready"] = (
                                focused["call"] == "verified"
                                and focused["fits"]
                                and not focused["key_dropped"]
                                and not focused["key_clipped"]
                                and not focused["unresolved_conditions"]
                                and not focused["later_events"]
                            )
                            mine[case["id"]] = row
                        rows.append(row)
                for case, *_ in built:
                    # A pair is told apart only when this member's own packet passes and the other
                    # member's packet does not pass for it. Missing evidence separates nothing.
                    others = [
                        o for o, *_ in built if o["family"] == case["family"] and o is not case
                    ]
                    own = mine[case["id"]]["passes"]
                    apart = [
                        o["id"] for o in others if own and not score(case, focus[o["id"]])["passes"]
                    ]
                    mine[case["id"]] |= {"siblings": len(others), "told_apart": apart}
    return rows


def report(rows):
    """Per packet and budget, over the rows that are decisions on a session prefix:

    runs, complete (every fact, nothing absent), mean shares of facts and marks held, and for the
    focus packet ready (call verified, fits, nothing key dropped or cut), false_ready (reported
    ready without being complete) and separable (pairs within a family told apart, where a pair
    counts only if the member's own packet passes). Stale rows are counted, not scored.
    """
    table = {}
    for row in rows:
        cell = table.setdefault((row["packet"], row["budget"]), Counter())
        if row["stale"]:
            cell["stale"] += 1
            continue
        cell["n"] += 1
        for key in ("complete", "facts", "marks"):
            cell[key] += row[key]
        if row["packet"] == "focus":
            cell["ready"] += row["ready"]
            cell["false_ready"] += row["ready"] and not row["complete"]
            cell["pairs"] += row["siblings"]
            cell["apart"] += len(row["told_apart"])
    out = []
    for (packet, budget), cell in sorted(table.items()):
        n = max(1, cell["n"])
        item = {"packet": packet, "budget": budget, "runs": cell["n"], "stale_runs": cell["stale"]}
        item |= {key: round(cell[key] / n, 3) for key in ("complete", "facts", "marks")}
        if packet == "focus":
            wrong, ready = cell["false_ready"], cell["ready"]
            item["ready"] = round(ready / n, 3)
            item |= {"ready_runs": ready, "ready_incomplete_runs": wrong}
            # Two rates with two denominators: of all runs, and of the runs reported ready.
            item["ready_incomplete_of_all"] = round(wrong / n, 3)
            item["ready_incomplete_of_ready"] = round(wrong / ready, 3) if ready else None
            item["separable"] = round(cell["apart"] / max(1, cell["pairs"]), 3)
        out.append(item)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cases", action="append", required=True, help="session cases (JSONL)")
    parser.add_argument("--embedder", choices=("embeddinggemma", "hash"), default="embeddinggemma")
    parser.add_argument("--model", default=None, help="a fine-tuned model directory")
    parser.add_argument("--device", help="cuda, mps or cpu; detected if omitted")
    parser.add_argument("--seeds", type=int, default=3, help="entity draws per case")
    parser.add_argument("--out", help="write one row per run to this JSONL file")
    args = parser.parse_args(argv)

    from summarizer.embedder import create
    from summarizer.summarize import Runtime

    if args.embedder == "hash":
        embedder = create("hash")
    else:
        embedder = create(**({"model": args.model} if args.model else {}), device=args.device)
    probes = args.model if args.model and (Path(args.model) / "probes.jsonl").is_file() else None
    cases = [case for path in args.cases for case in read_jsonl(path)]
    rows = run(cases, Runtime(embedder, probes, strict=True), seeds=args.seeds)
    if args.out:
        write_jsonl(args.out, rows)
    for item in report(rows):
        print(json.dumps(item))
    for row in rows:
        if row["packet"] == "focus" and row["seed"] == 0 and not row["stale"]:
            keys = ("case", "agent", "budget", "facts", "marks", "complete", "ready")
            if not row["passes"] or (row["ready"] and not row["complete"]):
                print(json.dumps({"shortfall": {key: row[key] for key in keys}}))
            if len(row["told_apart"]) < row["siblings"] and row["passes"]:
                print(json.dumps({"confusable": {"case": row["case"], "budget": row["budget"]}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
