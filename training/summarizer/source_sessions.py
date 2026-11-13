"""Run agent sessions over public repositories to grow the pool of real seed sources.

    python training/summarizer/source_sessions.py --plan training/summarizer/data/starter/source-plan.jsonl \\
        --out training/summarizer/data/seeds --workers 4

Seeds must come from many independent sources, and the saved transcripts of two projects are
few. This runs one read-only Codex session per line of a plan, each in a shallow clone of a
public repository, with this project's hook logging the session into a private spool. What the
agent reads there (build files, logs, documentation, test output) is real text from a source of
its own. training/summarizer/export_seeds.py then turns the spool into seeds like any other.

A plan line is {"source", "repo", "task"}: a short name for the source, the repository to clone,
and what to ask the agent to do there. Sessions are read-only and nothing is installed into
~/.codex: the hook is passed on the command line. Under --out it writes

    sources/<source>/        the clones
    spool/                   the session logs (SUMMARIZER_HOME)
    sources.jsonl            one line per session: {"block", "source", "repo", "commit", "task",
                             "events", "model"}, the private ledger of which block came from where

All of it is private working data: keep --out under training/summarizer/data/seeds/, which Git ignores. Two
sessions over the same repository are two blocks from one source, and the ledger says so.
"""

import argparse
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import read_jsonl
from summarizer.cli import hook_path, hooks_config
from summarizer.store import EVENTS


def _toml(value):
    if isinstance(value, dict):
        return "{" + ", ".join(f"{key} = {_toml(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    return json.dumps(value)


def hook_args(executable):
    """The -c options that give a Codex run this project's hook without touching its config."""
    args = []
    for event, groups in hooks_config("codex_code", executable)["hooks"].items():
        args += ["-c", f"hooks.{event}={_toml(groups)}"]
    return args


def block_of(agent, session):
    """The block name export_seeds.py gives a session, so the ledger and the seeds agree."""
    return "block-" + hashlib.sha256(f"{agent}/{session}".encode()).hexdigest()[:10]


def clone(repo, target):
    if not (target / ".git").exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "clone", "--quiet", "--depth", "25", repo, str(target)]
        subprocess.run(command, check=True, timeout=300, stdin=subprocess.DEVNULL)
    done = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def run_one(item, out, model, effort, timeout):
    """Clone a source, run one session in it, and return its ledger line (or the failure)."""
    work, spool = out / "sources" / item["source"], out / "spool"
    try:
        commit = clone(item["repo"], work)
    except (subprocess.SubprocessError, OSError) as error:
        return {"source": item["source"], "error": f"clone: {type(error).__name__}"}
    before = {path.parent.name for path in spool.glob(f"sessions/codex_code/*/{EVENTS}")}
    command = ["codex", "exec", "-m", model, "-c", f'model_reasoning_effort="{effort}"']
    command += ["--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral"]
    command += ["--dangerously-bypass-hook-trust", *hook_args(hook_path()), "-C", str(work)]
    env = {**os.environ, "SUMMARIZER_HOME": str(spool)}
    try:
        done = subprocess.run(
            [*command, item["task"]],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        status = done.returncode
    except subprocess.TimeoutExpired:
        status = "timeout"
    # The session's log is the one whose first event names this working directory.
    found = None
    for path in spool.glob(f"sessions/codex_code/*/{EVENTS}"):
        if path.parent.name in before:
            continue
        with open(path, encoding="utf-8") as handle:
            first = json.loads(handle.readline() or "{}")
        if (first.get("event") or {}).get("cwd") == str(work):
            found = path
    if found is None:
        return {"source": item["source"], "error": f"no session was logged (exit {status})"}
    with open(found, encoding="utf-8") as handle:
        events = sum(1 for _ in handle)
    line = {"block": block_of("codex_code", found.parent.name), "source": item["source"]}
    line |= {"repo": item["repo"], "commit": commit, "task": item["task"], "events": events}
    return line | {"model": f"{model}/{effort}", "exit": status}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--plan", required=True, help="sources and tasks (JSONL)")
    parser.add_argument("--out", required=True, help="private working directory")
    parser.add_argument("--model", default="gpt-6-luna", help="the agent that does the reading")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=420, help="seconds per session")
    parser.add_argument(
        "--limit", type=int, help="run only the first N lines not yet in the ledger"
    )
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser().resolve()
    ledger = out / "sources.jsonl"
    done = (
        {(row["source"], row["task"]) for row in read_jsonl(ledger)} if ledger.exists() else set()
    )
    plan = [item for item in read_jsonl(args.plan) if (item["source"], item["task"]) not in done]
    plan = plan[: args.limit] if args.limit else plan
    out.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    with ThreadPoolExecutor(args.workers) as pool:

        def work(item):
            return run_one(item, out, args.model, args.effort, args.timeout)

        for line in pool.map(work, plan):
            if "error" in line:
                failed += 1
                print(json.dumps(line))
                continue
            ok += 1
            with open(ledger, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line) + "\n")
    print(json.dumps({"planned": len(plan), "logged": ok, "failed": failed, "ledger": str(ledger)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
