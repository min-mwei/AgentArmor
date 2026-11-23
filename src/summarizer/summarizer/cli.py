"""Command line for the summarizer: run the daemon, read summaries, print hook configuration."""

import argparse
import fcntl
import importlib.util
import json
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from .store import (
    AGENTS,
    EVENTS,
    SUMMARY,
    home,
    list_sessions,
    load_summary,
    read_events,
    session_dir,
    view,
    write_jsonl_atomic,
)
from .text import render

# Events each agent can deliver that carry session evidence. Everything is observed; nothing blocks.
HOOK_EVENTS = {
    "claude_code": (
        "SessionStart",
        "InstructionsLoaded",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "PermissionDenied",
        "PostToolUse",
        "PostToolUseFailure",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "PostCompact",
        "ConfigChange",
        "SessionEnd",
    ),
    "codex_code": (
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "PostCompact",
        "SessionEnd",
    ),
}


def hook_path():
    beside = Path(sys.executable).parent / "summarizer-hook"
    return str(beside) if beside.exists() else shutil.which("summarizer-hook") or str(beside)


def hooks_config(agent, executable):
    """The hooks block for an agent's settings, calling executable for every observed event."""
    if agent == "claude_code":  # exec form: no shell between Claude Code and the hook
        handler = {"type": "command", "command": executable, "args": ["--agent", agent]}
    else:
        handler = {"type": "command", "command": f'"{executable}" --agent {agent}'}
    hooks = {}
    for event in HOOK_EVENTS[agent]:
        # SessionEnd keeps the agents' short default; a longer timeout would hold up their exit.
        entry = dict(handler) if event == "SessionEnd" else {**handler, "timeout": 10}
        hooks[event] = [{"hooks": [entry]}]
    return {"hooks": hooks}


def hooks_toml(config):
    """The same hooks as inline tables for Codex's config.toml."""
    lines = []
    for event, groups in config["hooks"].items():
        lines.append(f"[[hooks.{event}]]\n")
        for handler in groups[0]["hooks"]:
            lines.append(f"[[hooks.{event}.hooks]]")
            for key, value in handler.items():
                lines.append(f"{key} = {value}" if isinstance(value, int) else f"{key} = '{value}'")
            lines.append("")
    return "\n".join(lines)


def _runtime(args):
    from .summarize import Config, load

    config = Config(max_chars=args.max_chars, vector_dim=args.vector_dim)
    options = {"revision": args.revision, "device": args.device}
    options["local_files_only"] = args.local_files_only
    return load(args.model, args.probes, args.embedder, config, **options)


def _serve(args):
    from .daemon import Daemon

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    daemon = Daemon(_runtime(args), catchup_hours=args.catchup_hours, index=not args.no_index)
    try:
        daemon.serve(stop)
    except KeyboardInterrupt:
        pass
    return 0


def _pick(args):
    if args.session:
        return args.agent, args.session
    sessions = [s for s in list_sessions() if args.agent in (None, s[0])]
    if not sessions:
        raise SystemExit("no sessions have been logged yet")
    return sessions[0][0], sessions[0][1]


def _print(records, args):
    """Print a summary: its records as JSON Lines, or its text digest."""
    if args.jsonl:
        for record in records:
            print(json.dumps(record, ensure_ascii=False))
    else:
        summary = view(records)
        if args.max_chars or args.call:
            print(render(summary, args.max_chars or 3000, focus=args.call))
        else:
            print(summary["text"])


def _summarize(args):
    from .summarize import Session

    agent, name = _pick(args)
    directory = session_dir(agent, name)
    session = Session(agent, name, _runtime(args))
    records, session.log_bytes = read_events(directory / EVENTS, limit=args.upto)
    for record in records:
        session.add(record)
    session.flush()
    records = session.summary()
    if args.write:
        write_jsonl_atomic(directory / SUMMARY, records)
    _print(records, args)
    return 0


def _show(args):
    agent, name = _pick(args)
    summary = load_summary(agent, name, wait=args.wait)
    if summary is None:
        raise SystemExit(f"no summary for {agent}/{name} yet; is `summarizer serve` running?")
    if not summary["fresh"]:
        print("note: newer events are logged than this summary covers", file=sys.stderr)
    with open(session_dir(agent, name) / SUMMARY, encoding="utf-8") as handle:
        _print([json.loads(line) for line in handle if line.strip()], args)
    return 0


def _list(args):
    for agent, name, _, mtime in list_sessions():
        summary = load_summary(agent, name)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        if summary is None:
            print(f"{stamp}  {agent:11}  {name}  no summary")
            continue
        session = summary["session"]
        marks = " ".join(f"{level}={count}" for level, count in session["marked"].items())
        state = "fresh" if summary["fresh"] else "stale"
        events = session["coverage"]["events"]
        print(f"{stamp}  {agent:11}  {name}  events={events}  {marks}  {state}")
    return 0


def _daemon_running():
    try:
        with open(home() / "run" / "daemon.lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _doctor(args):
    from .embedder import MODEL, REVISION

    cache = os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub"
    )
    snapshot = os.path.join(cache, "models--" + MODEL.replace("/", "--"), "snapshots", REVISION)
    report = {
        "home": str(home()),
        "hook": hook_path(),
        "hook_installed": os.path.exists(hook_path()),
        "daemon_running": _daemon_running(),
        "sessions": len(list_sessions()),
        "runtime_installed": importlib.util.find_spec("sentence_transformers") is not None,
        "model": MODEL,
        "revision": REVISION,
        "model_cached": os.path.isdir(snapshot),
    }
    print(json.dumps(report, indent=1))
    return 0


def _hooks_config(args):
    if args.format == "toml" and args.agent != "codex_code":
        raise SystemExit("--format toml is for Codex's config.toml; Claude Code settings are JSON")
    config = hooks_config(args.agent, hook_path())
    print(hooks_toml(config) if args.format == "toml" else json.dumps(config, indent=2))
    return 0


def _prune(args):
    from .index import Index, path

    cutoff, removed = time.time() - args.days * 86400, []
    for agent, name, directory, mtime in list_sessions():
        if mtime < cutoff:
            shutil.rmtree(directory)
            removed.append((agent, name))
            print(f"removed {agent}/{name}")
    if removed and path().is_file():  # what is gone from the spool is gone from the index
        if _daemon_running():  # the daemon is the index's one writer: it forgets them itself
            print("the search index forgets them when the daemon next starts", file=sys.stderr)
            return 0
        try:
            with Index(write=True) as index:
                for agent, name in removed:
                    index.forget(agent, name)
        except Exception as error:  # noqa: BLE001 - the logs are removed either way
            print(f"the search index still lists them: {type(error).__name__}", file=sys.stderr)
    return 0


def _index(args):
    """The search index: how far it has read, or read every log again."""
    from .index import Index, IndexError_, behind, forget_missing, path, record

    sessions = list_sessions()
    if args.action == "status":
        try:
            index = Index()
        except FileNotFoundError:
            index = None
        except IndexError_ as error:
            raise SystemExit(f"index: {error}") from None
        told = index.status() if index is not None else {"file": str(path()), "present": False}
        told |= {"logged_sessions": len(sessions)}
        told |= {f"sessions_{gap}": len(found) for gap, found in behind(index, sessions).items()}
        print(json.dumps(told, indent=1))
        return 0
    if _daemon_running():
        raise SystemExit("index rebuild: stop the daemon first; it is what writes the index")
    from .embedder import create
    from .summarize import Runtime, Session

    # A rebuild reads the logs again and embeds nothing: the index holds no mark and no vector,
    # so no model is loaded for it.
    runtime, kept = Runtime(create("hash")), set()
    with Index(write=True) as index:
        for agent, name, directory, _ in reversed(sessions):  # oldest first
            session = Session(agent, name, runtime)
            records, session.log_bytes = read_events(directory / EVENTS)
            for item in records:
                session.add(item)
            record(index, agent, name, session)
            kept.add((agent, name))
        told = {"rebuilt": len(kept), "forgotten": forget_missing(index, sessions)}
        told = index.status() | told
    print(json.dumps(told, indent=1))
    return 0


def _search(args):
    """Find pieces of logged sessions by a name they hold or by words in their text."""
    from .index import Index, IndexError_, shown

    try:
        with Index() as index:
            if args.name is not None:
                hits = index.naming(args.name, args.kind, args.before, args.limit)
            else:
                hits = index.holding(args.text, args.before, args.limit)
    except FileNotFoundError:
        raise SystemExit(
            "search: no index yet; run the daemon or `summarizer index rebuild`"
        ) from None
    except IndexError_ as error:
        raise SystemExit(f"search: {error}") from None
    for hit in hits:
        print(shown(hit))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="summarizer", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def model_options(command):
        command.add_argument(
            "--embedder",
            choices=("embeddinggemma", "hash"),
            default="embeddinggemma",
            help="hash is a model-free bag-of-words stand-in for dry runs",
        )
        command.add_argument(
            "--model",
            default="google/embeddinggemma-2",
            help="Hugging Face id or a fine-tuned model directory",
        )
        command.add_argument("--revision", help="Hugging Face commit; defaults to the pinned one")
        command.add_argument("--device", help="cuda, mps or cpu; detected if omitted")
        command.add_argument(
            "--local-files-only",
            action="store_true",
            help="never download: require the model in the local cache",
        )
        command.add_argument("--probes", help="probes.jsonl to use instead of the built-in probes")
        command.add_argument("--vector-dim", type=int, default=256, choices=(128, 256, 512, 768))

    def session_options(command):
        command.add_argument("--agent", choices=AGENTS)
        command.add_argument("--session", help="session id; the most recent session if omitted")
        command.add_argument(
            "--jsonl", action="store_true", help="print the summary records as JSON Lines"
        )
        command.add_argument(
            "--call", help="tool_use_id of a call to center the digest on, as a guard would"
        )

    serve = commands.add_parser("serve", help="run the daemon that keeps summaries current")
    model_options(serve)
    serve.add_argument("--max-chars", type=int, default=3000, help="budget of the stored digest")
    serve.add_argument(
        "--catchup-hours",
        type=float,
        default=24.0,
        help="on start, summarize logs changed within this many hours",
    )
    serve.add_argument(
        "--no-index", action="store_true", help="do not keep the search index (index.sqlite)"
    )
    serve.set_defaults(run=_serve)

    once = commands.add_parser("summarize", help="summarize one session's log without the daemon")
    model_options(once)
    session_options(once)
    once.add_argument("--max-chars", type=int, default=3000)
    once.add_argument(
        "--write", action="store_true", help="also replace the session's summary.jsonl"
    )
    once.add_argument(
        "--upto",
        type=int,
        help="summarize only the first N events: the session as it stood at an earlier decision",
    )
    once.set_defaults(run=_summarize)

    show = commands.add_parser("show", help="print a session's current summary")
    session_options(show)
    show.add_argument("--max-chars", type=int, help="re-render the digest at this budget")
    show.add_argument("--wait", type=float, default=0.0, help="seconds to wait for a fresh summary")
    show.set_defaults(run=_show)

    commands.add_parser("list", help="list logged sessions").set_defaults(run=_list)
    commands.add_parser("doctor", help="report local setup").set_defaults(run=_doctor)

    config = commands.add_parser("hooks-config", help="print hook settings to merge into an agent")
    config.add_argument("--agent", choices=AGENTS, required=True)
    config.add_argument("--format", choices=("json", "toml"), default="json")
    config.set_defaults(run=_hooks_config)

    index = commands.add_parser("index", help="the search index over logged sessions")
    index.add_argument("action", choices=("status", "rebuild"))
    index.set_defaults(run=_index)

    search = commands.add_parser("search", help="find pieces of logged sessions; a hit is a lead")
    what = search.add_mutually_exclusive_group(required=True)
    what.add_argument("--name", help="a path, host or identifier, exactly as it was named")
    what.add_argument("--text", help="words the text holds, as one phrase")
    search.add_argument("--kind", help="with --name: only names of this kind, such as path")
    search.add_argument("--before", type=float, help="only what was logged before this time")
    search.add_argument("--limit", type=int, default=50)
    search.set_defaults(run=_search)

    prune = commands.add_parser("prune", help="delete session logs and summaries by age")
    prune.add_argument("--days", type=float, required=True, help="remove sessions idle this long")
    prune.set_defaults(run=_prune)

    args = parser.parse_args(argv)
    if args.command in ("summarize", "show") and args.session and not args.agent:
        parser.error("--session needs --agent")
    if args.command == "summarize" and args.upto is not None and args.write:
        parser.error("--upto summarizes a prefix of the log; it cannot replace the summary")
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
