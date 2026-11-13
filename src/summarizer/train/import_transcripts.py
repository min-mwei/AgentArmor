"""Replay saved agent transcripts as hook events, so sessions from before the hooks can seed data.

    python train/import_transcripts.py --project ~/repos/Summarizer --project ~/repos/AgentArmor \\
        --out train/data/seeds/spool

Claude Code keeps a transcript per session under ~/.claude/projects and Codex under
~/.codex/sessions. This reads the ones whose working directory is inside a named project and
writes each as an event log in a spool of its own, through the hook's own redaction and clipping.
train/export_seeds.py then reads that spool like any other.

Only sessions of the projects you name are read. The spool holds real session text: keep it under
train/data/seeds/, which Git ignores. A transcript is not a hook stream, so the result is good for
seeds and nothing else: approvals, denials and instruction files are not reconstructed.
"""

import argparse
import json
import os
from pathlib import Path

from summarizer.hook import build_record
from summarizer.store import append_event


def _lines(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content if isinstance(b, dict)]
        return "\n".join(part for part in parts if isinstance(part, str))
    return ""


def claude_events(path):
    """Hook-shaped events for one Claude Code transcript: prompts, tool calls and their results."""
    calls = {}
    for record in _lines(path):
        message = record.get("message")
        if record.get("type") not in ("user", "assistant") or not isinstance(message, dict):
            continue
        common = {"session_id": str(record.get("sessionId")), "cwd": record.get("cwd") or ""}
        content = message.get("content")
        if record["type"] == "user" and isinstance(content, str):
            if not record.get("isMeta"):
                yield {**common, "hook_event_name": "UserPromptSubmit", "prompt": content}
            continue
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                call = {"tool_name": str(block.get("name")), "tool_input": block.get("input")}
                call["tool_use_id"] = str(block.get("id"))
                calls[call["tool_use_id"]] = call
                yield {**common, "hook_event_name": "PreToolUse", **call}
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                call = calls.pop(block["tool_use_id"])
                if block.get("is_error"):
                    failed = {
                        "hook_event_name": "PostToolUseFailure",
                        "error": _text(block.get("content")),
                    }
                    yield {**common, **call, **failed}
                else:
                    result = record.get("toolUseResult") or _text(block.get("content"))
                    yield {
                        **common,
                        "hook_event_name": "PostToolUse",
                        **call,
                        "tool_response": result,
                    }
            elif (
                block.get("type") == "text"
                and record["type"] == "user"
                and not record.get("isMeta")
            ):
                yield {
                    **common,
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": block.get("text") or "",
                }


def codex_events(path):
    """Hook-shaped events for one Codex session file."""
    common, calls = {"session_id": Path(path).stem, "cwd": ""}, {}
    for record in _lines(path):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        kind = payload.get("type")
        if record.get("type") == "session_meta":
            common = {
                "session_id": str(payload.get("id") or Path(path).stem),
                "cwd": payload.get("cwd") or "",
            }
        elif kind == "item_completed" and (payload.get("item") or {}).get("type") == "UserMessage":
            prompt = _text(payload["item"].get("content"))
            yield {**common, "hook_event_name": "UserPromptSubmit", "prompt": prompt}
        elif kind in ("custom_tool_call", "function_call"):
            command = payload.get("input") or payload.get("arguments") or ""
            call = {"tool_name": "Bash", "tool_input": {"command": str(command)}}
            call["tool_use_id"] = str(payload.get("call_id"))
            calls[call["tool_use_id"]] = call
            yield {**common, "hook_event_name": "PreToolUse", **call}
        elif kind in ("custom_tool_call_output", "function_call_output"):
            call = calls.pop(str(payload.get("call_id")), None)
            output = payload.get("output")
            if call:
                result = output if isinstance(output, str) else _text(output)
                yield {**common, "hook_event_name": "PostToolUse", **call, "tool_response": result}


def transcripts(projects, claude_home, codex_home):
    """(agent, path) of every transcript whose working directory is inside one of projects."""
    roots = [str(Path(project).expanduser().resolve()) for project in projects]

    def inside(cwd):
        return any(cwd == root or cwd.startswith(root + "/") for root in roots)

    for path in sorted(Path(claude_home).expanduser().glob("projects/*/*.jsonl")):
        cwd = next((r["cwd"] for r in _lines(path) if isinstance(r.get("cwd"), str)), "")
        if inside(cwd):
            yield "claude_code", path
    for path in sorted(Path(codex_home).expanduser().glob("sessions/**/*.jsonl")):
        first = next(_lines(path), {})
        payload = first.get("payload") if isinstance(first.get("payload"), dict) else {}
        if inside(str(payload.get("cwd") or "")):
            yield "codex_code", path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--project", action="append", required=True, help="read sessions run here")
    parser.add_argument("--out", required=True, help="spool directory to write (keep it untracked)")
    parser.add_argument("--claude-home", default="~/.claude")
    parser.add_argument("--codex-home", default="~/.codex")
    args = parser.parse_args(argv)

    os.environ["SUMMARIZER_HOME"] = str(Path(args.out).expanduser().resolve())
    counts = {"claude_code": [0, 0], "codex_code": [0, 0]}
    for agent, path in transcripts(args.project, args.claude_home, args.codex_home):
        events = claude_events(path) if agent == "claude_code" else codex_events(path)
        start = {"hook_event_name": "SessionStart", "source": "startup"}
        written = 0
        for event in events:
            if not written:
                keys = ("session_id", "cwd")
                append_event(
                    agent,
                    event["session_id"],
                    build_record(agent, {**{k: event[k] for k in keys}, **start}),
                )
            append_event(agent, event["session_id"], build_record(agent, event))
            written += 1
        counts[agent][0] += bool(written)
        counts[agent][1] += written
    print(json.dumps({agent: {"sessions": s, "events": e} for agent, (s, e) in counts.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
