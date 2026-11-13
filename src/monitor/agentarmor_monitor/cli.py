"""AgentArmor's local monitor.

    agentarmor-monitor hooks-config --agent claude_code     # hooks to merge into the agent
    agentarmor-monitor serve --kai-package DIR              # the local server, Kai in it
    agentarmor-monitor serve --kai-url URL --kai-package-sha256 HASH    # Kai elsewhere
    agentarmor-monitor detections

Summarizer is the local model: its hook records everything an agent reads and runs, and the
local server keeps a labelled summary of each session. Whatever it marks at or above --level
is put to Kai with the rules and that summary as context, and Kai's decision (allow or deny)
is appended to the session's detections.jsonl.

Two ways to connect an agent. With the observer hook the server only watches and records
Kai's decisions. With the gate (hooks-config --gate) a suspicious tool call waits while the
gate asks the local server about it, and a "deny" is returned to the agent. The gate talks to
the local server and to nothing else. Kai's serving path is unqualified (policy_ready false).

    agentarmor-monitor requests --session ID     # what would be sent to Kai, without sending
    agentarmor-monitor render scripts.jsonl --out DIR     # the same for scripted sessions
    agentarmor-monitor tally DIR/calls.jsonl     # what the gate would do with every call
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading

from .escalate import LEVELS, append, detect, key_of, latest, read, settled, triggers
from .kai import TOKEN_ENV, KaiClient, KaiMismatch, KaiUnavailable, Unsent, bearer, loopback
from .kai import profile as kai_profile
from .local import PORT
from .policy import writable


def _expected(args):
    """The decision model the operator configured, or None when no package was named."""
    named = getattr(args, "kai_package_sha256", None)
    return kai_profile(named) if named else None


def _kai(args):
    """A client for the Kai address in args. Raises ValueError or OSError when it cannot be one.

    Only the local server and the diagnostics an operator runs by hand come here: a gate has
    no way to a decision model. A token named in a file is used as the operator says. One
    found in the environment is sent to a service elsewhere only, never to whatever happens
    to listen on a port of this machine.
    """
    token = bearer(args.kai_token_file, environment=not loopback(args.kai_url))
    return KaiClient(args.kai_url, args.kai_timeout, args.allow_remote, token, _expected(args))


def _client(args):
    try:
        return _kai(args)
    except (OSError, ValueError) as error:
        raise SystemExit(f"kai: {error}") from None


def _rules(args):
    from .policy import load_rules

    try:
        return load_rules(args.policy)
    except (OSError, ValueError) as error:
        raise SystemExit(f"policy: {error}") from None


def _gate(args):
    """The gate hook. It asks the local server, on this machine, and holds nothing else: no
    rules, no way to a decision model and no credential for one."""
    from .gate import main as gate
    from .local import Local

    def prepare():
        # Made inside the gate's own guard: an address that is wrong must end in the gate's
        # denial, not in a bare exit before the event was even read.
        if args.retired:
            # An option a gate once had: rules, a level, a Kai address, a token. None of it
            # is the gate's any more, and none of it is used. A gate started with one has
            # not started, so that nobody takes it to be running under what they gave it.
            raise ValueError("a gate has no option for the rules or for a decision model")
        return Local(args.server, args.timeout)

    return gate(args, prepare)


def _runtime(args):
    """Summarizer's runtime: the encoder, with the probes that were calibrated for it.

    The built-in probe numbers fit the pinned base model and no other, and Summarizer drops
    them for any other encoder: its three probe labels are then never given. A fine-tuned
    model directory carries its own (probes.jsonl), and they are loaded with it. A directory
    without them is refused unless the operator asks for it by name (--uncalibrated): a
    server that quietly marks less must not look like one that found nothing to mark.
    """
    from summarizer.embedder import MODEL, create
    from summarizer.summarize import Runtime

    if args.embedder == "hash":
        return Runtime(create("hash"))
    model, probes = args.model or MODEL, getattr(args, "probes", None)
    uncalibrated = getattr(args, "uncalibrated", False)
    if probes is None and os.path.isfile(os.path.join(model, "probes.jsonl")):
        probes = model  # a fine-tuned model directory carries its calibrated probes
    if probes is None and os.path.isdir(model) and not uncalibrated:
        raise SystemExit(
            "summarizer: that model directory has no probes.jsonl, and the built-in probes are "
            "calibrated for the pinned base model only. Give its probes with --probes, or "
            "--uncalibrated to run it knowing that it gives no probe label"
        )
    encoder = create(model=model, device=args.device, local_files_only=args.local_files_only)
    try:
        runtime = Runtime(encoder, probes)
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f"summarizer: the probes cannot be used: {error}") from None
    if not runtime.calibrated and not uncalibrated:
        raise SystemExit(
            "summarizer: none of those probes is calibrated, so no probe label would be given. "
            "Calibrate them for this model, or say --uncalibrated"
        )
    return runtime


def _judge(args):
    """(the decision model behind the local server, where it is, what it must be).

    Where: local, remote or none. What it must be is the operator's word (kai.profile): for a
    model at another address the package named with --kai-package-sha256, which is required;
    for one loaded here the package that was loaded, which must be the named one if one was.
    """
    from .server import Absent, Relay, load_kai

    here = args.kai_package or args.kai_revision
    try:
        named = _expected(args)
    except ValueError as error:
        raise SystemExit(f"kai: {error}") from None
    if here and args.kai_url:
        raise SystemExit("kai: give a Kai to load here or a Kai address, not both")
    if here:
        found = load_kai(
            args.kai_package, args.kai_revision, args.kai_cache_dir, args.kai_local_files_only
        )
        try:
            loaded = kai_profile(found.metadata().get("package_sha256"))
        except ValueError:
            raise SystemExit("kai: the model loaded here does not name its package") from None
        if named is not None and named != loaded:
            raise SystemExit("kai: the package loaded is not the one --kai-package-sha256 names")
        return found, "local", loaded
    if args.kai_url:
        if named is None:
            raise SystemExit(
                "kai: say which package the model at that address must be, with "
                "--kai-package-sha256: an answer from anything else is not used"
            )
        return Relay(_client(args)), "remote", named
    if named is not None:
        raise SystemExit("kai: --kai-package-sha256 names a model, and none is configured")
    return Absent(), "none", None


def mode_of(args):
    return "shadow" if getattr(args, "shadow", False) else "enforce"


def gate_level(args):
    """What a call must be marked at for a gate's question to go to Kai.

    Named, it is what the operator named. Otherwise medium for a server whose decisions are
    applied: a denial blocks a call, and Kai's decisions on real calls are unmeasured. And
    low in shadow, where nothing is blocked and the point is to measure every marked call.
    """
    return args.gate_level or ("low" if mode_of(args) == "shadow" else "medium")


def _serve(args):
    """Run the local server: Summarizer and its model, the rules, and the decision model."""
    from .daemon import Monitor
    from .decide import Decider
    from .server import Front, InProcess, free, listen

    rules = _rules(args)
    if args.at_once < 1:
        raise SystemExit("serve: --at-once is how many calls are decided at once, at least 1")
    if not free(args.port):  # asked first: a model takes a while to load
        raise SystemExit(f"port {args.port} on this machine is taken; choose another with --port")
    # A wrong address, package or hash ends here, before any model loads.
    judge, place, expected = _judge(args)
    runtime = _runtime(args)
    front = Front(judge, place, runtime, expected)
    asks = InProcess(front) if place != "none" else None
    if asks is not None:
        try:
            asks.identify()
        except KaiMismatch as error:  # something answers, and it is not what was configured
            raise SystemExit(f"kai: what answers is not the configured model ({error})") from None
        except KaiUnavailable:  # it may come up later: watching and marking do not wait for it
            print("kai: the decision model does not answer yet", file=sys.stderr, flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    monitor = Monitor(runtime, asks, args.level, args.max_chars)
    monitor.rules = rules
    decider = Decider(
        asks, rules, gate_level(args), args.max_chars, args.wait, args.at_once, mode=mode_of(args)
    )
    try:
        api, _ = listen(front, decider, args.port, monitor, args.model_api)
    except OSError as error:
        raise SystemExit(f"port {args.port} on this machine is taken: {error}") from None
    try:
        monitor.serve(stop)
    except KeyboardInterrupt:
        pass
    finally:
        api.shutdown()
        api.server_close()
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
        print(writable(json.dumps(record, ensure_ascii=False)))
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


def _tally(args):
    """Count what the gate would do with every proposed call of a render.

    It reads the ledger and no session text.
    """
    from .tally import read, tally

    try:
        answers = read(args.answers) if args.answers else None
        labels = read(args.labels) if args.labels else None
        result = tally(read(args.calls), answers, labels, args.on_error)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"tally: {type(error).__name__}: {error}") from None
    print(json.dumps(result, indent=2))
    return 0


def _detections(args):
    for agent, name, directory, _ in _sessions(args):
        for record in latest(read(directory)).values():
            if args.flagged and not record.get("flagged"):
                continue
            if args.disagreements and not record.get("disagreement"):
                continue
            told = {"agent": agent, "session": name} | record
            print(writable(json.dumps(told, ensure_ascii=False)))
    return 0


# What a client does when a gate's hook does not run at all: its executable is missing, it
# cannot start, or the client's own deadline ends it. The gate's own guard never sees those.
FAILURE_BLOCK = (2, 1, 295)  # the first Claude Code that can block a call when a hook fails
CLIENTS = {"claude_code": "claude", "codex_code": "codex"}
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def client_version(agent, run=subprocess.run):
    """(major, minor, patch) of the agent's client on this machine, or None if it cannot be
    told: no such program on PATH, or an answer with no version in it."""
    program = shutil.which(CLIENTS[agent])
    if program is None:
        return None
    try:
        told = run([program, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    found = _VERSION.search(told.stdout or "")
    return tuple(int(part) for part in found.groups()) if found else None


def gates_summary(records):
    """What gates answered, counted from their own records and the server's: how many calls
    they were run for, how many were put to the decision model, what was and would have been
    answered, why checks were not completed, how long an answer took, and how many calls
    are reported as run after a "deny". Counts only: no text of any session."""
    answers = [r for r in records if r.get("stage") == "answered"]
    issued = [r for r in records if r.get("gate") and r.get("stage") == "issued"]
    observed = [r for r in records if r.get("stage") == "observed"]
    took = sorted(r["elapsed_ms"] for r in answers if isinstance(r.get("elapsed_ms"), int))

    def at(share):
        return took[min(len(took) - 1, int(share * len(took)))] if took else None

    undecided = {}
    for record in answers:
        if record.get("verdict") == "undecided":
            undecided[str(record.get("why"))] = undecided.get(str(record.get("why")), 0) + 1
    told = {"calls": len(answers), "decisions_issued": len(issued)}
    told["by_mode"] = {
        mode: sum(1 for r in answers if r.get("mode") == mode) for mode in ("enforce", "shadow")
    }
    told["put_to_kai"] = sum(
        1 for r in issued if ((r.get("routing") or {}).get("requests_sent") or 0) > 0
    )
    told["kai_denied"] = sum(1 for r in answers if r.get("verdict") == "deny")
    told["would_deny"] = sum(1 for r in answers if r.get("would") == "deny")
    told["answered_deny"] = sum(1 for r in answers if r.get("answered") == "deny")
    told["undecided"] = dict(sorted(undecided.items()))
    told["no_receipt"] = sum(1 for r in answers if not r.get("receipt"))
    told["elapsed_ms"] = {"measured": len(took), "median": at(0.5), "p95": at(0.95)}
    told["elapsed_ms"]["max"] = took[-1] if took else None
    # A report by the order it came in beside the gate's answer. One recorded with no answer
    # is counted as that only while no answer has been tied to it since.
    after = [r for r in observed if r.get("order", "after_answer") == "after_answer"]
    before = [r for r in observed if r.get("order") == "before_answer"]
    tied = {(r.get("session"), r.get("call_id"), r.get("reported")) for r in before}
    alone = [
        r
        for r in observed
        if r.get("order") == "no_answer"
        and (r.get("session"), r.get("call_id"), r.get("reported")) not in tied
    ]
    told["reported_after_an_answer"] = len(after)
    told["reported_before_the_answer"] = len(before)
    told["reported_with_no_answer"] = len(alone)
    told["ran_after_deny"] = sum(1 for r in observed if r.get("ran_after_deny"))
    told["ran_before_answer"] = sum(1 for r in observed if r.get("ran_before_answer"))
    return told | {"advisory": True, "policy_ready": False}


def _gates(args):
    records = []
    for _, _, directory, _ in _sessions(args):
        records += read(directory)
    print(json.dumps(gates_summary(records), indent=2))
    return 0


def _hooks(args):
    from summarizer.cli import hook_path, hooks_config, hooks_toml

    config = hooks_config(args.agent, args.hook or hook_path())
    if args.gate:  # the gate records every event itself, so it replaces the observer hook
        config = hooks_config(args.agent, args.gate)
        for event, groups in config["hooks"].items():
            for handler in groups[0]["hooks"]:
                words = "gate --shadow" if args.shadow else "gate"
                if "args" in handler:
                    handler["args"] = [*words.split(), *handler["args"]]
                else:
                    handler["command"] = handler["command"].replace(
                        f'"{args.gate}"', f'"{args.gate}" {words}'
                    )
                handler["timeout"] = (
                    max(handler.get("timeout", 0), 60) if "timeout" in handler else 60
                )
                # The one handler that decides. Every other event is only recorded, and a
                # recording that fails must not stop the agent.
                # A shadow gate stops nothing, so it must not stop a call by failing either.
                blocks = args.failure_block and not args.shadow
                if event == "PreToolUse" and args.agent == "claude_code" and blocks:
                    handler["onFailure"] = "block"
        if args.shadow:
            print(
                "agentarmor-monitor: a shadow gate stops nothing. It asks the local server "
                "about every call and records what it would have answered.",
                file=sys.stderr,
            )
        else:
            _say_what_a_failed_hook_does(args)
    elif args.shadow:
        raise SystemExit("hooks-config: --shadow is a setting of the gate: give --gate as well")
    print(hooks_toml(config) if args.toml else json.dumps(config, indent=2))
    return 0


def _say_what_a_failed_hook_does(args):
    """On standard error, beside the configuration: what holds when the gate cannot run."""
    told = sys.stderr
    if args.agent == "codex_code":
        print(
            "agentarmor-monitor: Codex has no setting that stops a call when a hook cannot "
            "start or is ended by Codex's own deadline: the call then runs. The gate stops "
            "only the calls it is run for and answers in time. Keep Codex's approvals and "
            "sandbox on, and note that input sent to a running process (write_stdin) is not "
            "put to a hook at all.",
            file=told,
        )
        return
    needed = ".".join(str(part) for part in FAILURE_BLOCK)
    if not args.failure_block:
        print(
            "agentarmor-monitor: without onFailure, a gate that cannot start or is ended by "
            "Claude Code's deadline does not stop the call.",
            file=told,
        )
        return
    have = client_version("claude_code")
    if have is None:
        print(
            f"agentarmor-monitor: the PreToolUse hook is set to stop the call when the hook "
            f'itself fails (onFailure: "block"). That needs Claude Code {needed} or later; '
            f"the version on this machine could not be told.",
            file=told,
        )
    elif have < FAILURE_BLOCK:
        raise SystemExit(
            f'hooks-config: onFailure: "block" needs Claude Code {needed} or later, and this '
            f"machine has {'.'.join(str(part) for part in have)}. Update it, or pass "
            f"--no-failure-block and accept that a gate that cannot run does not stop the call"
        )


def _doctor(args):
    from summarizer.cli import hook_path
    from summarizer.store import home

    from .local import Local, ServerUnavailable

    report = {"monitor": "summarizer", "spool": str(home()), "hook": hook_path()}
    report |= {"policy_ready": False, "advisory": True, "server": args.server}
    try:
        # What the local server says of itself: what is loaded, how far behind it is, where
        # the decision model is and whether it is the one configured.
        report["local_server"] = Local(args.server, args.timeout).health()
    except ValueError as error:
        raise SystemExit(f"server: {error}") from None
    except ServerUnavailable as error:
        report["local_server"] = None
        report["server_error"] = str(error)
    print(json.dumps(report, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def kai(command, url="http://127.0.0.1:8009"):
        """A way to a Kai service: for the local server, and for a diagnostic run by hand."""
        command.add_argument(
            "--kai-url",
            default=url,
            help="where a Kai service answers" + ("" if url else "; none unless given"),
        )
        command.add_argument("--kai-timeout", type=float, default=30.0)
        command.add_argument(
            "--allow-remote",
            action="store_true",
            help="send packets to a Kai service that is not on this machine (https only)",
        )
        command.add_argument(
            "--kai-token-file",
            help="a file holding the bearer token of that service; for a service that is not "
            f"on this machine, ${TOKEN_ENV} is used when no file is given",
        )
        command.add_argument(
            "--kai-package-sha256",
            help="the package that service must be: an answer from anything else is not used",
        )

    def server(command):
        """The way to the local server: a gate has this, and no way to a decision model."""
        command.add_argument(
            "--server",
            default=f"http://127.0.0.1:{PORT}",
            help="where AgentArmor's local server answers: an address on this machine",
        )
        command.add_argument("--timeout", type=float, default=45.0)

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
        command.add_argument(
            "--probes",
            help="probes.jsonl calibrated for --model, or a directory holding it; a model "
            "directory's own probes.jsonl is used when this is omitted",
        )
        command.add_argument(
            "--uncalibrated",
            action="store_true",
            help="run a model that has no calibrated probes: it ranks and links, and gives "
            "none of the three probe labels",
        )

    serve = commands.add_parser(
        "serve",
        help="run the local server: Summarizer and its model, with Kai in it or behind it",
    )
    # Where the decision model is. With none of these the server watches and marks only.
    kai(serve, url=None)
    serve.add_argument("--kai-package", help="load Kai in this server from a local export")
    serve.add_argument(
        "--kai-revision", help="load Kai in this server from a full commit of its HF repository"
    )
    serve.add_argument("--kai-cache-dir", help="Hugging Face cache for --kai-revision")
    serve.add_argument("--kai-local-files-only", action="store_true")
    serve.add_argument(
        "--port", type=int, default=PORT, help="the loopback port this server answers on"
    )
    trigger(serve, "low")  # watching sends on everything marked: recall first
    # A denial blocks a call and Kai's decisions on real calls are unmeasured, so a gate's
    # call is put to Kai only from medium up.
    serve.add_argument(
        "--gate-level",
        choices=tuple(LEVELS),
        help="what Summarizer must mark a call at for a gate's question to go to Kai: "
        "medium unless given, and low with --shadow",
    )
    serve.add_argument(
        "--shadow",
        action="store_true",
        help="issue every decision as a shadow one: gates record what they would have "
        "answered and stop nothing, and every marked call is put to Kai",
    )
    serve.add_argument(
        "--wait", type=float, default=3.0, help="seconds a decision waits for the summary"
    )
    serve.add_argument(
        "--at-once", type=int, default=4, help="how many calls are decided at the same time"
    )
    serve.add_argument(
        "--model-api",
        action="store_true",
        help="also answer /v1/systemone: any program on this machine can then have its own "
        "text put to the decision model, with this server's credential if the model is remote",
    )
    embedder(serve)
    serve.set_defaults(run=_serve)

    scan = commands.add_parser("scan", help="send one session's flagged entries to Kai once")
    kai(scan)
    trigger(scan, "low")
    scan.add_argument("--agent", choices=("claude_code", "codex_code"))
    scan.add_argument("--session")
    scan.set_defaults(run=_scan)

    gate = commands.add_parser(
        "gate", help="hook: record every event, and ask the local server about a tool call"
    )
    # The rules, the level and the decision model are the local server's. A gate has an
    # address on this machine to ask, and what to do when it gets no verdict.
    server(gate)

    class Retired(argparse.Action):
        """An option a gate had before the server owned the decision. Its value is dropped
        here and never used; _gate() then refuses to start (see there)."""

        def __call__(self, parser, namespace, values, option_string=None):
            namespace.retired = [*namespace.retired, option_string]

    gate.set_defaults(retired=[])
    for name in ("--kai-url", "--kai-timeout", "--kai-token-file", "--level", "--max-chars"):
        gate.add_argument(name, action=Retired, help=argparse.SUPPRESS)
    for name in ("--policy", "--wait"):
        gate.add_argument(name, action=Retired, help=argparse.SUPPRESS)
    gate.add_argument("--allow-remote", action=Retired, nargs=0, help=argparse.SUPPRESS)
    gate.add_argument("--agent", choices=("claude_code", "codex_code"), required=True)
    gate.add_argument(
        "--on-error",
        choices=("deny", "abstain"),
        default="deny",
        help="what to do when no decision can be reached for a call",
    )
    gate.add_argument(
        "--shadow",
        action="store_true",
        help="stop nothing: ask and record as always, with what would have been answered",
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

    counted = commands.add_parser(
        "tally", help="count what the gate would do with every proposed call of a render"
    )
    counted.add_argument("calls", help="calls.jsonl of a render")
    counted.add_argument("--answers", help="Kai's answers: request_sha256 and decision per line")
    counted.add_argument("--labels", help="the kit's labels: root_id, view_id, event, should")
    counted.add_argument(
        "--on-error",
        choices=("deny", "abstain"),
        default="deny",
        help="what the gate does when no decision can be reached, as it was run",
    )
    counted.set_defaults(run=_tally)

    hooks = commands.add_parser("hooks-config", help="print the observer hooks for an agent")
    hooks.add_argument("--agent", choices=("claude_code", "codex_code"), required=True)
    hooks.add_argument("--hook", help="absolute path of summarizer-hook; found if omitted")
    hooks.add_argument("--toml", action="store_true", help="Codex config.toml form")
    hooks.add_argument(
        "--gate",
        help="absolute path of agentarmor-monitor: use the deciding gate, not the plain observer",
    )
    hooks.add_argument(
        "--shadow",
        action="store_true",
        help="with --gate: a gate that stops nothing and records what it would have answered",
    )
    hooks.add_argument(
        "--no-failure-block",
        dest="failure_block",
        action="store_false",
        help="with --gate for Claude Code: leave out onFailure, for a client older than "
        + ".".join(str(part) for part in FAILURE_BLOCK),
    )
    hooks.set_defaults(run=_hooks)

    counts = commands.add_parser(
        "gates", help="count what gates answered: calls, questions to Kai, denials, latency"
    )
    counts.add_argument("--agent", choices=("claude_code", "codex_code"))
    counts.add_argument("--session")
    counts.set_defaults(run=_gates)

    doctor = commands.add_parser(
        "doctor", help="report the local setup and what the local server says of itself"
    )
    server(doctor)
    doctor.set_defaults(run=_doctor)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
