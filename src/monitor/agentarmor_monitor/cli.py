"""AgentArmor's local monitor.

    agentarmor-monitor hooks-config --agent claude_code     # hooks to merge into the agent
    agentarmor-monitor serve --kai-url http://127.0.0.1:8009
    agentarmor-monitor detections

Summarizer is the local model: its hook records everything an agent reads and runs, and its
daemon keeps a labelled summary of each session. Whatever it marks at or above --level is sent
to Kai with the rules and that summary as context, and Kai's decision (allow or deny) is
appended to the session's detections.jsonl.

Two ways to connect an agent. With the observer hook the monitor only watches and records
Kai's decisions. With the gate (hooks-config --gate) a suspicious tool call waits for Kai and a
"deny" is returned to the agent. Kai's serving path is unqualified (policy_ready false).

    agentarmor-monitor requests --session ID     # what would be sent to Kai, without sending
    agentarmor-monitor render scripts.jsonl --out DIR     # the same for scripted sessions
"""

import argparse
import json
import signal
import sys
import threading

from .escalate import LEVELS, append, detect, key_of, latest, read, settled, triggers
from .kai import KaiClient, KaiUnavailable, Unsent


def _client(args):
    try:
        return KaiClient(args.kai_url, args.kai_timeout, args.allow_remote)
    except ValueError as error:
        raise SystemExit(str(error)) from None


def _rules(args):
    from .policy import load_rules

    try:
        return load_rules(args.policy)
    except (OSError, ValueError) as error:
        raise SystemExit(f"policy: {error}") from None


def _gate(args):
    from .gate import main as gate
    from .policy import load_rules

    def prepare():
        # Run inside the gate's own guard: a policy file or Kai address that is wrong must end
        # in the gate's denial, not in a bare exit before the event was even read.
        client = KaiClient(args.kai_url, args.kai_timeout, args.allow_remote)
        options = {"level": args.level, "max_chars": args.max_chars, "wait": args.wait}
        return client, options | {"rules": load_rules(args.policy)}

    return gate(args, prepare)


def _runtime(args):
    from summarizer.embedder import MODEL, create
    from summarizer.summarize import Runtime

    if args.embedder == "hash":
        return Runtime(create("hash"))
    return Runtime(
        create(
            model=args.model or MODEL, device=args.device, local_files_only=args.local_files_only
        )
    )


def _serve(args):
    from .daemon import Monitor

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    monitor = Monitor(_runtime(args), _client(args), args.level, args.max_chars)
    monitor.rules = _rules(args)
    try:
        monitor.serve(stop)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.close()
    return 0


def _sessions(args):
    from summarizer.store import list_sessions

    found = [s for s in list_sessions() if args.agent in (None, s[0])]
    if args.session:
        found = [s for s in found if s[1] == args.session]
    if not found:
        raise SystemExit("no such session has been logged")
    return found


def _scan(args):
    """Escalate one session's current summary once, without the daemon or the embedding model."""
    from summarizer.store import load_summary

    client, rules = _client(args), _rules(args)
    agent, name, directory, _ = _sessions(args)[0]
    summary = load_summary(agent, name)
    if summary is None:
        raise SystemExit(
            "that session has no summary yet: run the monitor, or summarizer summarize"
        )
    done, asked = settled(read(directory)), {}  # one summary: the same request is sent once
    for entry in triggers(summary, args.level):
        if key_of(entry) in done:
            continue
        record = detect(
            client, summary, entry, args.max_chars, rules, directory=directory, asked=asked
        )
        append(directory, record)
        print(json.dumps(record, ensure_ascii=False))
    return 0


def _requests(args):
    """Print the request each trigger of a session would send to Kai. Nothing is sent or kept.

    The output holds session text: treat it like the session's own log.
    """
    from summarizer.store import load_summary

    rules = _rules(args)
    agent, name, directory, _ = _sessions(args)[0]
    summary = load_summary(agent, name)
    if summary is None:
        raise SystemExit(
            "that session has no summary yet: run the monitor, or summarizer summarize"
        )
    first = {}  # request hash -> the first trigger that builds it
    for entry in triggers(summary, args.level):
        unsent = Unsent()
        record = detect(unsent, summary, entry, args.max_chars, rules, directory=directory)
        kept = ("key", "trigger", "layout", "summary_embedding", "summary_detector")
        kept += ("packet", "review", "rules")
        shown = {key: record[key] for key in kept}
        # Sendable means a request was built. A trigger refused for its size has none, and
        # says why, so nothing counts it as input the service could be given.
        shown |= {"sendable": unsent.request is not None, "request": unsent.request}
        if unsent.request is None:
            shown["refused"] = record["status"]
        else:  # the daemon would put this request to Kai once, for the first trigger
            same = first.setdefault(record["packet"]["request_sha256"], record["key"])
            if same != record["key"]:
                shown["same_request_as"] = same
        print(json.dumps(shown))
    return 0


def _render(args):
    """Render scripted sessions into the requests the monitor would build. Nothing is sent.

    The output holds session text: treat it like a session's own log.
    """
    from .render import read_scripts, render

    try:
        read_scripts(args.scripts)  # a wrong input is refused before any model is loaded
        manifest = render(
            args.scripts, args.out, _runtime(args), args.max_chars, args.level, args.gate_level
        )
    except (OSError, ValueError) as error:
        raise SystemExit(f"render: {error}") from None
    print(json.dumps({"counts": manifest["counts"], "outputs": manifest["outputs"]}))
    return 0


def _detections(args):
    for agent, name, directory, _ in _sessions(args):
        for record in latest(read(directory)).values():
            if args.flagged and not record.get("flagged"):
                continue
            if args.disagreements and not record.get("disagreement"):
                continue
            print(json.dumps({"agent": agent, "session": name} | record, ensure_ascii=False))
    return 0


def _hooks(args):
    from summarizer.cli import hook_path, hooks_config, hooks_toml

    config = hooks_config(args.agent, args.hook or hook_path())
    if args.gate:  # the gate records every event itself, so it replaces the observer hook
        config = hooks_config(args.agent, args.gate)
        for groups in config["hooks"].values():
            for handler in groups[0]["hooks"]:
                if "args" in handler:
                    handler["args"] = ["gate", *handler["args"]]
                else:
                    handler["command"] = handler["command"].replace(
                        f'"{args.gate}"', f'"{args.gate}" gate'
                    )
                handler["timeout"] = (
                    max(handler.get("timeout", 0), 60) if "timeout" in handler else 60
                )
    print(hooks_toml(config) if args.toml else json.dumps(config, indent=2))
    return 0


def _doctor(args):
    from summarizer.cli import hook_path
    from summarizer.store import home

    report = {"monitor": "summarizer", "spool": str(home()), "hook": hook_path()}
    report |= {"policy_ready": False, "advisory": True, "kai_url": args.kai_url}
    try:
        report["kai"] = _client(args).health()
    except KaiUnavailable as error:
        report["kai"] = None
        report["kai_error"] = str(error)
    print(json.dumps(report, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def kai(command):
        command.add_argument("--kai-url", default="http://127.0.0.1:8009")
        command.add_argument("--kai-timeout", type=float, default=30.0)
        command.add_argument(
            "--allow-remote",
            action="store_true",
            help="send packets to a Kai service that is not on this machine",
        )

    def trigger(command, level):
        command.add_argument(
            "--level",
            choices=tuple(LEVELS),
            default=level,
            help=f"what Summarizer must mark an entry at for it to go to Kai (default {level})",
        )
        command.add_argument(
            "--max-chars",
            type=int,
            default=5000,
            help="budget for the state Kai reads: the rules, the packet and the call",
        )
        command.add_argument(
            "--policy", help="rules for Kai's decision (JSONL of id, tier, description)"
        )

    def embedder(command):
        command.add_argument(
            "--embedder", choices=("embeddinggemma", "hash"), default="embeddinggemma"
        )
        command.add_argument(
            "--model", help="a local EmbeddingGemma 2 directory; the pinned model if omitted"
        )
        command.add_argument("--device", help="cuda, mps or cpu; detected if omitted")
        command.add_argument("--local-files-only", action="store_true")

    serve = commands.add_parser(
        "serve", help="run Summarizer's daemon and send what it flags to Kai"
    )
    kai(serve)
    trigger(serve, "low")  # watching sends on everything marked: recall first
    embedder(serve)
    serve.set_defaults(run=_serve)

    scan = commands.add_parser("scan", help="send one session's flagged entries to Kai once")
    kai(scan)
    trigger(scan, "low")
    scan.add_argument("--agent", choices=("claude_code", "codex_code"))
    scan.add_argument("--session")
    scan.set_defaults(run=_scan)

    gate = commands.add_parser(
        "gate", help="hook: record every event, and let Kai decide a suspicious tool call"
    )
    kai(gate)
    # A gate denial blocks a call and Kai's decisions on real calls are unmeasured, so the
    # gate asks Kai only from medium up.
    trigger(gate, "medium")
    gate.add_argument("--agent", choices=("claude_code", "codex_code"), required=True)
    gate.add_argument("--wait", type=float, default=3.0, help="seconds to wait for the summary")
    gate.add_argument(
        "--on-error",
        choices=("deny", "abstain"),
        default="deny",
        help="what to do when no decision can be reached for a call",
    )
    gate.set_defaults(run=_gate)

    shown = commands.add_parser("detections", help="print detections, the latest per trigger")
    shown.add_argument("--agent", choices=("claude_code", "codex_code"))
    shown.add_argument("--session")
    shown.add_argument("--flagged", action="store_true", help="only those Kai flagged")
    shown.add_argument(
        "--disagreements",
        action="store_true",
        help="only those Summarizer marked medium or higher and Kai cleared",
    )
    shown.set_defaults(run=_detections)

    unsent = commands.add_parser(
        "requests", help="print what a session's triggers would send to Kai, without sending"
    )
    trigger(unsent, "low")
    unsent.add_argument("--agent", choices=("claude_code", "codex_code"))
    unsent.add_argument("--session")
    unsent.set_defaults(run=_requests)

    scripted = commands.add_parser(
        "render", help="render scripted sessions into the requests the monitor would build"
    )
    scripted.add_argument(
        "scripts", help="one view per line: root_id, view_id, agent, rules, events"
    )
    scripted.add_argument("--out", required=True, help="a folder for the three files it writes")
    scripted.add_argument(
        "--level", choices=tuple(LEVELS), default="low", help="what the watcher sends from"
    )
    scripted.add_argument(
        "--gate-level", choices=tuple(LEVELS), default="medium", help="what the gate sends from"
    )
    scripted.add_argument(
        "--max-chars",
        type=int,
        default=5000,
        help="budget for the state Kai reads: the rules, the packet and the call",
    )
    embedder(scripted)
    scripted.set_defaults(run=_render)

    hooks = commands.add_parser("hooks-config", help="print the observer hooks for an agent")
    hooks.add_argument("--agent", choices=("claude_code", "codex_code"), required=True)
    hooks.add_argument("--hook", help="absolute path of summarizer-hook; found if omitted")
    hooks.add_argument("--toml", action="store_true", help="Codex config.toml form")
    hooks.add_argument(
        "--gate",
        help="absolute path of agentarmor-monitor: use the deciding gate, not the plain observer",
    )
    hooks.set_defaults(run=_hooks)

    doctor = commands.add_parser("doctor", help="report the local setup and whether Kai answers")
    kai(doctor)
    doctor.set_defaults(run=_doctor)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
