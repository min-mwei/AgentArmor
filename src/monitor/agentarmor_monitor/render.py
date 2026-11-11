"""Render scripted sessions into the requests the monitor would put to Kai. Nothing is sent.

    agentarmor-monitor render scripts.jsonl --out rendered/

A script is a session written as the hook events an agent would deliver, with the rules it
runs under. Each line of the input is one view of one root:

    {"root_id", "view_id", "agent", "rules": [{"id", "tier", "description"}], "events": [...]}

and may add "files" ({path: text} of instruction files), "links" ({path: where it really
leads}) and "codex_home". The events go, one at a time and in order, through Summarizer's own
hook entry into a spool of the render's own, and Summarizer's daemon brings the summary up to
date after each. So what is rendered at an event is the session as it stood at that event:
earlier results, approvals and withdrawals are in it, and nothing later is.

Nothing is read from the machine the render runs on. The hook asks the disk where a path
really leads and what an instruction file holds; here the script answers (files and links),
and a path the script does not mention leads to itself and holds nothing. Summarizer reads
"~" as the home the events' transcript_path lies under, so the first event must carry one:
a script that does not say is refused, because the only other home is this machine's. The
same script therefore renders the same on any machine. The user's own spool is never read
or written.

Three files are written to --out, and nothing at all when a problem is found:

- requests.jsonl: one row for each trigger, at the event where it first reached the level.
  A row holds the exact request (state and the registered questions) and the account of it:
  which routes would send it, whether it could be built, whether it is a review from before
  the call, what the packet left out and by which section, the entries its text shows with
  their trust, the session's coverage at that event, and what embedded and what marked the
  summary. pre_action_complete is true when all three hold that a decision example needs
  from a render: the request was built, the evidence is complete, and it is from before the
  call. It is not a label and admits nothing.
- calls.jsonl: one row for every proposed call (every PreToolUse event), asked about or not.
  It says how Summarizer marked the call at its proposal and what each route would do with
  it: the watcher, which sends from --level up, and the gate, which sends from --gate-level
  up and otherwise stands aside or stops by default. A call neither route asks about is in
  this file and in no other. Count from here: a later, retrospective request about the same
  call does not change what happened at its proposal.
- manifest.json: what rendered (this package's source, the layout, the questions, the policy
  interpretation), what the summaries say embedded and marked them, the options, the hash of
  the input, counts, and the hash of each output.

Every view is rendered twice and the two must be equal, or nothing is written. That catches
what varies within one process. It does not prove the model gives the same scores in another
process or on another device: run the command again and compare the manifests' output hashes.

Events are fed one at a time. A live daemon may embed several events in one batch, and a
score can then differ in its last digits.

The output holds session text: treat it like a session's own log.
"""

import contextlib
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from summarizer.daemon import Daemon
from summarizer.events import agent_home
from summarizer.hook import Scripted, call_sha256, handle
from summarizer.store import AGENTS, load_summary, safe_id, session_dir, view

from .escalate import detect, key_of, triggers
from .gate import decide
from .kai import QUESTIONS, Unsent
from .policy import INTERPRETATION, LAYOUT, check_rules

SCHEMA = "agentarmor-render/v1"
REQUESTS, CALLS, MANIFEST = "requests.jsonl", "calls.jsonl", "manifest.json"
REQUIRED = ("root_id", "view_id", "agent", "rules", "events")
OPTIONAL = ("files", "links", "codex_home")
SESSION = "render"  # the session id of a script whose events carry none
# What a request row keeps of the record detect() builds. The request itself is added to it.
KEPT = ("key", "trigger", "review", "layout", "packet", "shown")
KEPT += ("summary_embedding", "summary_detector")


def _sha256(value):
    """SHA-256 of a value's canonical JSON: sorted keys, default separators, ASCII."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("ascii")).hexdigest()


def source_sha256():
    """SHA-256 over this package's source files, or None when any of them cannot be read.

    An installed package does not know its commit. This names the code that rendered, so two
    renders can be told to come from the same monitor.
    """
    try:
        files = sorted(Path(__file__).parent.glob("*.py"))
        return _sha256({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files})
    except OSError:
        return None


def _paths(value, what):
    """A {absolute path: text} map from a script, or ValueError."""
    good = isinstance(value, dict) and all(
        isinstance(path, str) and os.path.isabs(path) and isinstance(text, str)
        for path, text in value.items()
    )
    if not good:
        raise ValueError(f"{what} maps absolute paths to text")
    return value


def check_script(script):
    """A script line with its session id settled, or ValueError saying what is wrong with it.

    The message never quotes the script: a script holds session text.
    """
    if not isinstance(script, dict):
        raise ValueError("a line is one JSON object")
    if unknown := sorted(set(script) - set(REQUIRED) - set(OPTIONAL)):
        raise ValueError(f"unknown fields: {', '.join(unknown)}")
    for key in ("root_id", "view_id"):
        if not isinstance(script.get(key), str) or not script[key].strip():
            raise ValueError(f"{key} is a non-empty string")
    if script.get("agent") not in AGENTS:
        raise ValueError("agent is one of " + ", ".join(AGENTS))
    check_rules(script.get("rules"))
    if len({rule["id"] for rule in script["rules"]}) != len(script["rules"]):
        raise ValueError("rule ids are distinct")
    events = script.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("events is a list of one hook event or more")
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("hook_event_name"), str):
            raise ValueError("an event is an object with a hook_event_name")
    if agent_home(events[0].get("transcript_path")) is None:
        # Summarizer reads "~" as the home the transcript path lies under. Without one it
        # falls back on the home of whoever runs it, which a render must never show.
        raise ValueError(
            "the first event has no transcript_path under a .claude or .codex folder, so "
            "nothing says where the agent's home is"
        )
    given = {event.get("session_id") for event in events if "session_id" in event}
    if not given:
        events = [event | {"session_id": SESSION} for event in events]
    elif len(given) > 1 or any("session_id" not in event for event in events):
        raise ValueError("the events of one view carry one session_id, or none carries any")
    elif not all(isinstance(name, str) and name for name in given):
        raise ValueError("session_id is a non-empty string")
    for key in ("files", "links"):
        _paths(script.get(key, {}), key)
    home = script.get("codex_home")
    if home is not None and not (isinstance(home, str) and os.path.isabs(home)):
        raise ValueError("codex_home is an absolute path")
    return script | {"events": events}


@contextlib.contextmanager
def scratch_spool():
    """A spool of the render's own, so that nothing is read from or written to the user's."""
    held = os.environ.get("SUMMARIZER_HOME")
    with tempfile.TemporaryDirectory(prefix="agentarmor-render-") as path:
        os.environ["SUMMARIZER_HOME"] = path
        try:
            yield path
        finally:
            if held is None:
                os.environ.pop("SUMMARIZER_HOME", None)
            else:
                os.environ["SUMMARIZER_HOME"] = held


def request_row(about, number, record, request, routes, coverage):
    """One row of requests.jsonl: a request and the account of it."""
    row = about | {"event": number, "routes": routes}
    row |= {key: record.get(key) for key in KEPT}
    # Sendable means a request was built. One refused for its size has none, and says why.
    row |= {
        "sendable": request is not None,
        "questions": list((request or {}).get("questions", ())),
    }
    if request is None:
        row["refused"] = record["status"]
    proposed_call = record["trigger"]["type"] == "action" and record["review"] == "pre_action"
    row["pre_action_complete"] = bool(
        request is not None and proposed_call and record["packet"]["complete"]
    )
    return row | {"coverage": coverage, "request": request}


def _watch(summary, entry, options, directory):
    """(record, request) for a trigger as the watching daemon would build it."""
    unsent = Unsent()
    record = detect(
        unsent, summary, entry, options["max_chars"], options["rules"], directory=directory,
        explain=True,
    )  # fmt: skip
    return record, unsent.request


def _gate(agent, payload, options):
    """(what the gate would do with a proposed call, record or None, request or None).

    The gate's own decide() is run with a client that sends nothing, so the route is the
    gate's and not a copy of it. What comes back is one of: asked (a request was built),
    abstain (not marked at the gate's level, in a session scanned whole), or undecided with
    the reason, which the gate turns into a denial unless it was started with --on-error
    abstain.
    """
    unsent = Unsent()
    verdict, record, why = decide(
        agent, payload, unsent, options["gate_level"], options["max_chars"], 0.0,
        options["rules"], explain=True,
    )  # fmt: skip
    route = {"level": options["gate_level"]}
    if unsent.request is not None:
        route |= {"asked": True, "request_sha256": record["packet"]["request_sha256"]}
        # What the gate makes of an "allow": it stands only on complete evidence from before
        # the call. A "deny" always stands.
        if not record["packet"]["complete"]:
            route["an_allow"] = "incomplete"
        elif record["review"] != "pre_action":
            route["an_allow"] = "moved_on"
        else:
            route["an_allow"] = "stands"
        return route, record, unsent.request
    if verdict == "abstain":
        return route | {"asked": False, "outcome": "abstain", "why": "below_level"}, None, None
    route |= {"asked": False, "outcome": "undecided", "why": why}
    refused = record is not None and "trigger" in record  # a call refused for its size
    return route, (record if refused else None), None


def render_view(script, runtime, max_chars=5000, level="low", gate_level="medium"):
    """(request rows, call rows) for one checked script."""
    agent, events = script["agent"], script["events"]
    session = events[0]["session_id"]
    about = {"root_id": script["root_id"], "view_id": script["view_id"], "agent": agent}
    about["rules"] = {"ids": [rule["id"] for rule in script["rules"]]}
    about["rules"]["sha256"] = _sha256(script["rules"])
    options = {"max_chars": max_chars, "gate_level": gate_level, "rules": script["rules"]}
    machine = Scripted(script.get("files"), script.get("links"), script.get("codex_home"))
    requests, calls, seen = [], [], set()
    with scratch_spool():
        daemon = Daemon(runtime, log=lambda message: None)
        name, directory = safe_id(session), session_dir(agent, session)
        for number, payload in enumerate(events, 1):
            handle(agent, json.dumps(payload).encode("ascii"), machine)
            daemon.update(agent, name)
            # The watcher's view: the daemon's own summary, copied as its monitor copies it.
            summary = json.loads(json.dumps(view(daemon.sessions[agent, name].summary())))
            # The size of the log depends on how the clock's digits print, not on the session.
            coverage = {
                key: value
                for key, value in summary["session"]["coverage"].items()
                if key != "log_bytes"
            }
            rows = {}  # trigger key -> its row at this event; a gate's own row under ("gate", key)
            for entry in triggers(summary, level):
                if key_of(entry) in seen:
                    continue
                seen.add(key_of(entry))
                record, request = _watch(summary, entry, options, directory)
                rows[record["key"]] = request_row(
                    about, number, record, request, ["watch"], coverage
                )
            if payload["hook_event_name"] == "PreToolUse":
                calls.append(_call(about, number, payload, options, rows, coverage, level))
            requests += rows.values()
    # A request two triggers of one view build alike is the same question asked twice.
    first = {}
    for row in requests:
        digest = row["packet"].get("request_sha256") if row["sendable"] else None
        if digest is not None and first.setdefault(digest, row["key"]) != row["key"]:
            row["same_request_as"] = first[digest]
    return requests, calls


def _same(one, other):
    """Whether two routes built the same request, or were refused for the same reason."""
    if one["sendable"] and other["sendable"]:
        return one["packet"]["request_sha256"] == other["packet"]["request_sha256"]
    return not one["sendable"] and not other["sendable"] and one["refused"] == other["refused"]


def _call(about, number, payload, options, rows, coverage, level):
    """One row of calls.jsonl for a proposed call. The gate's request, if it has one, joins rows."""
    agent, call_id = about["agent"], payload.get("tool_use_id")
    identity = call_sha256(payload.get("tool_name"), payload.get("tool_input"))
    row = about | {"event": number, "call_id": call_id, "call_sha256": identity}
    row["tool"] = payload.get("tool_name")
    # The call as Summarizer marked it at its proposal, read from the spool as the gate reads it.
    entry = None
    if isinstance(call_id, str) and call_id:
        summary = load_summary(agent, safe_id(payload["session_id"]), 0.0, call_id, identity)
        if summary is not None and summary.get("fresh") and summary.get("call") == "verified":
            actions = (e for e in summary["entries"] if e["type"] == "action")
            entry = next((e for e in actions if e.get("call_id") == call_id), None)
    row["summarized"] = entry is not None
    for key in ("level", "confidence", "labels"):
        row[key] = entry.get(key) if entry is not None else None
    watched = rows.get(key_of(entry)) if entry is not None else None
    row["watch"] = {"level": level, "asked": watched is not None}
    if watched is None:
        row["watch"]["why"] = "below_level" if entry is not None else "no_summary"
    elif watched["sendable"]:
        row["watch"]["request_sha256"] = watched["packet"]["request_sha256"]
    else:
        row["watch"]["refused"] = watched["refused"]
    route, record, request = _gate(agent, payload, options)
    row |= {"gate": route, "routes_agree": None}
    # Of the call's own request, whichever route built it; null when neither did.
    row["pre_action_complete"] = watched["pre_action_complete"] if watched is not None else None
    if record is not None:
        mine = request_row(about, number, record, request, ["gate"], coverage)
        row["pre_action_complete"] = mine["pre_action_complete"]
        if watched is not None:
            row["routes_agree"] = _same(watched, mine)
        if row["routes_agree"]:
            # One request on both routes. The row kept is the gate's, which held the call itself.
            rows[mine["key"]] = mine | {"routes": ["watch", "gate"]}
        else:
            rows["gate", mine["key"]] = mine
    return row


def _lines(rows):
    return "".join(json.dumps(row) + "\n" for row in rows)


def read_scripts(path):
    """(checked scripts, SHA-256 of the file). ValueError names the line and what is wrong."""
    data = Path(path).read_bytes()
    scripts, seen = [], set()
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            script = check_script(json.loads(line))
        except ValueError as error:
            said = "not JSON" if isinstance(error, json.JSONDecodeError) else str(error)
            raise ValueError(f"line {number}: {said}") from None
        if (script["root_id"], script["view_id"]) in seen:
            raise ValueError(f"line {number}: this root_id and view_id are used twice")
        seen.add((script["root_id"], script["view_id"]))
        scripts.append(script)
    if not scripts:
        raise ValueError("the file holds no script")
    return scripts, hashlib.sha256(data).hexdigest()


class Tally:
    """What a render holds, in counts. Every proposed call is in exactly one gate count."""

    def __init__(self):
        self.counts = Counter()
        self.by = {name: Counter() for name in ("calls_by_level", "gate", "gate_an_allow")}
        self.by |= {name: Counter() for name in ("requests_by_trigger", "requests_by_review")}
        self.roots, self.told = set(), {"summary_embedding": set(), "summary_detector": set()}
        self.packets = set()

    def add(self, script, requests, calls):
        self.roots.add(script["root_id"])
        self.counts.update(views=1, events=len(script["events"]), proposed_calls=len(calls))
        self.counts.update(requests=len(requests))
        for call in calls:
            gate = call["gate"]
            self.by["calls_by_level"][call["level"] or "unknown"] += 1
            self.by["gate"]["asked" if gate["asked"] else f"{gate['outcome']}:{gate['why']}"] += 1
            if gate["asked"]:
                self.by["gate_an_allow"][gate["an_allow"]] += 1
            self.counts["watch_asked"] += call["watch"]["asked"]
            self.counts["asked_by_neither_route"] += (
                not gate["asked"] and not call["watch"]["asked"]
            )
            self.counts["routes_disagree"] += call["routes_agree"] is False
        for row in requests:
            self.by["requests_by_trigger"][row["trigger"]["type"]] += 1
            self.by["requests_by_review"][row["review"]] += 1
            self.counts["requests_not_sendable"] += not row["sendable"]
            self.counts["requests_complete"] += bool(row["packet"]["complete"])
            self.counts["pre_action_complete"] += row["pre_action_complete"]
            self.packets.add(row["layout"]["packet"] or "")
            for key, seen in self.told.items():
                seen.add(json.dumps(row[key], sort_keys=True))

    def result(self):
        names = ("views", "events", "proposed_calls", "watch_asked", "asked_by_neither_route")
        names += ("routes_disagree", "requests", "requests_not_sendable", "requests_complete")
        out = {"roots": len(self.roots)} | {name: self.counts[name] for name in names}
        out["pre_action_complete"] = self.counts["pre_action_complete"]
        return out | {name: dict(sorted(count.items())) for name, count in self.by.items()}


def render(path, out, runtime, max_chars=5000, level="low", gate_level="medium"):
    """Render every script of a file into out and return the manifest.

    Raises ValueError, and leaves nothing behind, when the input is wrong, an output is
    already there, or two renders of a view differ.
    """
    out = Path(out)
    for name in (REQUESTS, CALLS, MANIFEST):
        if (out / name).exists():
            raise ValueError(f"{out / name} is already there: a render never replaces one")
    scripts, input_sha256 = read_scripts(path)
    out.mkdir(parents=True, exist_ok=True)
    partial = {name: out / (name + ".partial") for name in (REQUESTS, CALLS, MANIFEST)}
    tally, hashes, rows = Tally(), {}, Counter()
    try:
        files = {}
        for name in (REQUESTS, CALLS):
            fd = os.open(partial[name], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            files[name], hashes[name] = os.fdopen(fd, "w", encoding="ascii"), hashlib.sha256()
        with files[REQUESTS], files[CALLS]:
            for script in scripts:
                first = render_view(script, runtime, max_chars, level, gate_level)
                again = render_view(script, runtime, max_chars, level, gate_level)
                texts = [_lines(part) for part in first]
                if texts != [_lines(part) for part in again]:
                    where = f"root {script['root_id']}, view {script['view_id']}"
                    raise ValueError(f"{where}: two renders differ, so nothing was written")
                tally.add(script, *first)
                for name, text, part in zip((REQUESTS, CALLS), texts, first, strict=True):
                    files[name].write(text)
                    hashes[name].update(text.encode("ascii"))
                    rows[name] += len(part)
        manifest = {"schema": SCHEMA, "repeated": True}
        manifest["monitor"] = {"source_sha256": source_sha256()}
        manifest["layout"] = {
            "state": LAYOUT,
            "packet": sorted(tally.packets),
            "questions_sha256": _sha256(QUESTIONS),
            "interpretation_sha256": hashlib.sha256(INTERPRETATION.encode("utf-8")).hexdigest(),
        }
        # What the summaries themselves say, never what is running now. A render that built no
        # request has no summary to quote, and says nothing here.
        for key, told in tally.told.items():
            manifest[key] = [json.loads(item) for item in sorted(told)]
        manifest["options"] = {"max_chars": max_chars, "level": level, "gate_level": gate_level}
        manifest["input"] = {"file": Path(path).name, "sha256": input_sha256}
        manifest["counts"] = tally.result()
        manifest["outputs"] = {
            name: {"sha256": hashes[name].hexdigest(), "rows": rows[name]}
            for name in (REQUESTS, CALLS)
        }
        fd = os.open(partial[MANIFEST], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as file:
            file.write(json.dumps(manifest, indent=2) + "\n")
        for name, path_ in partial.items():
            os.replace(path_, out / name)
    finally:
        for path_ in partial.values():
            path_.unlink(missing_ok=True)
    return manifest
