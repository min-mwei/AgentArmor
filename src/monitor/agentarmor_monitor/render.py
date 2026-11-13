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

Three files are written to --out, which must be a new or an empty folder, and nothing at all
when a problem is found:

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
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from summarizer.daemon import Daemon
from summarizer.events import agent_home
from summarizer.hook import Scripted, call_sha256, handle
from summarizer.store import AGENTS, safe_id, session_dir, view

from .decide import judge
from .escalate import detect, key_of, triggers
from .kai import QUESTIONS, Unsent
from .policy import INTERPRETATION, LAYOUT, check_rules

SCHEMA = "agentarmor-render/v1"
REQUESTS, CALLS, MANIFEST = "requests.jsonl", "calls.jsonl", "manifest.json"
REQUIRED = ("root_id", "view_id", "agent", "rules", "events")
OPTIONAL = ("files", "links", "codex_home")
SESSION = "render"  # the session id of a script whose events carry none
# Every source file of this package. source_sha256() is over exactly these.
SOURCES = ("__init__.py", "cli.py", "daemon.py", "decide.py", "doctor.py", "escalate.py")
SOURCES += ("gate.py",)
SOURCES += ("kai.py", "local.py", "panels.py", "policy.py", "render.py", "server.py")
SOURCES += ("tally.py",)
# What a request row keeps of the record detect() builds. The request itself is added to it.
KEPT = ("key", "trigger", "review", "layout", "packet", "shown")
KEPT += ("summary_embedding", "summary_detector")


def _sha256(value):
    """SHA-256 of a value's canonical JSON: sorted keys, default separators, ASCII."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("ascii")).hexdigest()


def source_sha256(directory=None):
    """SHA-256 over this package's source files, or None when they cannot all be read.

    An installed package does not know its commit. This names the code that rendered, so two
    renders can be told to come from the same monitor. It is over the files named in SOURCES
    and no others, and it is unknown unless every one of them is read: a package installed as
    compiled files has no source to name, and a hash of nothing, or of the files that happen
    to be there, would look like an identity and be none.
    """
    directory = Path(__file__).parent if directory is None else Path(directory)
    try:
        found = {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in SOURCES
        }
    except OSError:
        return None
    return _sha256(found)


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
    if any(set(rule) != {"id", "tier", "description"} for rule in script["rules"]):
        raise ValueError("a rule holds id, tier and description, and nothing else")
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
    named = [event["session_id"] for event in events if "session_id" in event]
    if not named:
        events = [event | {"session_id": SESSION} for event in events]
    elif not all(isinstance(name, str) and name for name in named):
        raise ValueError("session_id is a non-empty string")
    elif len(named) < len(events) or len(set(named)) > 1:
        raise ValueError("the events of one view carry one session_id, or none carries any")
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

    The decision a gate gets from the local server (decide.judge) is run with a client that
    sends nothing, so the route is the real one and not a copy of it: the call is named as a
    gate names it, and read from the session's log. "triggered" says the call was marked at
    the gate's level,
    and "asked" that a request for Kai was built: a call too large to show is triggered and
    not asked. What comes back is one of: asked; abstain (not marked at the gate's level, in
    a session scanned whole); or undecided with the reason, which the gate turns into a
    denial unless it was started with --on-error abstain.
    """
    unsent = Unsent()
    identity = call_sha256(payload.get("tool_name"), payload.get("tool_input"))
    verdict, record, why = judge(
        agent, payload.get("session_id"), payload.get("tool_use_id"), identity, unsent,
        options["gate_level"], options["max_chars"], 0.0, options["rules"], explain=True,
    )  # fmt: skip
    route = {"level": options["gate_level"]}
    if unsent.request is not None:
        route |= {"triggered": True, "asked": True}
        route["request_sha256"] = record["packet"]["request_sha256"]
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
        route |= {"triggered": False, "asked": False, "outcome": "abstain", "why": "below_level"}
        return route, None, None
    refused = record is not None and "trigger" in record  # marked, and too large to be shown
    # Not marked at the level in a session with gaps is a stop too, but not a trigger. With
    # no summary of the call, or no id to find it by, whether it was marked is not known.
    marked = True if refused else False if why == "coverage" else None
    route |= {"triggered": marked, "asked": False, "outcome": "undecided", "why": why}
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
    requests, calls, watched = [], [], {}  # watched: trigger key -> the watcher's row for it
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
            rows = []  # the rows of this event, in order
            for entry in triggers(summary, level):
                if key_of(entry) in watched:
                    continue
                record, request = _watch(summary, entry, options, directory)
                row = request_row(about, number, record, request, ["watch"], coverage)
                watched[record["key"]] = row
                rows.append(row)
            if payload["hook_event_name"] == "PreToolUse":
                call, mine = _call(
                    about, number, payload, options, summary, watched, coverage, level
                )
                calls.append(call)
                here = [row for row in rows if mine is not None and row["key"] == mine["key"]]
                if here and call["routes_agree"]:
                    # One request on both routes, built at this event. The row kept is the
                    # gate's, which held the call itself; it takes the watcher's place.
                    mine["routes"] = ["watch", "gate"]
                    rows = [mine if row is here[0] else row for row in rows]
                    watched[mine["key"]] = mine
                elif mine is not None:
                    rows.append(mine)
            requests += rows
    # A request two triggers of one view build alike is the same question asked twice.
    first = {}
    for row in requests:
        digest = row["packet"].get("request_sha256") if row["sendable"] else None
        if digest is not None and first.setdefault(digest, row["key"]) != row["key"]:
            row["same_request_as"] = first[digest]
    reconcile(requests, calls)
    return requests, calls


def reconcile(requests, calls):
    """Check the ledger against the requests themselves, or raise ValueError.

    "asked" on a route means a request exists. So every call a route asks about names a
    request that is among the rows, built on that route, and every request built on the gate
    belongs to exactly one call. A count taken from the ledger is then a count of requests.
    """
    built = set(_built(requests))
    named = []
    for call in calls:
        for route in ("watch", "gate"):
            about = call[route]
            if about["asked"] != ("request_sha256" in about):
                raise ValueError("the ledger says a call was asked about and names no request")
            if about["asked"] and (route, about["request_sha256"]) not in built:
                raise ValueError("the ledger names a request that was not built on that route")
            named += [about["request_sha256"]] if about["asked"] and route == "gate" else []
    at_gate = [digest for route, digest in _built(requests) if route == "gate"]
    if sorted(named) != sorted(at_gate):
        raise ValueError("the requests built at the gate are not the ones the ledger names")


def _built(requests):
    """(route, request_sha256) for every request that exists, once for each route it is on."""
    return [
        (route, row["packet"]["request_sha256"])
        for row in requests
        if row["sendable"]
        for route in row["routes"]
    ]


def _same(one, other):
    """Whether two routes built the same request, or were refused for the same reason."""
    if one["sendable"] and other["sendable"]:
        return one["packet"]["request_sha256"] == other["packet"]["request_sha256"]
    return not one["sendable"] and not other["sendable"] and one["refused"] == other["refused"]


def _call(about, number, payload, options, summary, watched, coverage, level):
    """(one row of calls.jsonl for a proposed call, the gate's own request row or None).

    summary is the session at this event. The call's entry is found by its id, or, for a call
    the agent gave no id, as the action this event produced: such a call can still be watched,
    though the gate, which needs the id, cannot check it. On each route "triggered" says the
    call was marked at the route's level and "asked" that a request was built for it. A call
    marked and too large to show is triggered and not asked, on both.
    """
    agent, call_id = about["agent"], payload.get("tool_use_id")
    identity = call_sha256(payload.get("tool_name"), payload.get("tool_input"))
    row = about | {"event": number, "call_id": call_id, "call_sha256": identity}
    row["tool"] = payload.get("tool_name")
    actions = [e for e in summary["entries"] if e["type"] == "action"]
    entry = next((e for e in actions if call_id and e.get("call_id") == call_id), None)
    entry = entry or next((e for e in actions if not call_id and e["seq"] == number), None)
    # How Summarizer marked the call at its proposal; null when it made no entry of it.
    row["summarized"] = entry is not None
    for key in ("level", "confidence", "labels"):
        row[key] = entry.get(key) if entry is not None else None
    seen = watched.get(key_of(entry)) if entry is not None else None
    row["watch"] = {"level": level, "triggered": seen is not None, "asked": False}
    if entry is None:
        row["watch"] |= {"triggered": None, "why": "no_summary"}
    elif seen is None:
        row["watch"]["why"] = "below_level"
    elif seen["sendable"]:
        row["watch"] |= {"asked": True, "request_sha256": seen["packet"]["request_sha256"]}
    else:
        row["watch"]["why"] = seen["refused"]
    route, record, request = _gate(agent, payload, options)
    row |= {"gate": route, "routes_agree": None}
    # Of the call's own request, whichever route built it; null when neither built one.
    built = seen is not None and seen["sendable"]
    row["pre_action_complete"] = seen["pre_action_complete"] if built else None
    mine = None
    if record is not None:
        mine = request_row(about, number, record, request, ["gate"], coverage)
        if mine["sendable"]:
            row["pre_action_complete"] = mine["pre_action_complete"]
        if seen is not None:
            row["routes_agree"] = _same(seen, mine)
    return row, mine


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
    """What a render holds, in counts.

    Every proposed call is in exactly one gate count. "asked" counts requests that exist: a
    call marked and too large to show is counted as triggered and as refused, never as asked.
    """

    def __init__(self):
        self.counts = Counter()
        names = ("calls_by_level", "watch_refused", "gate", "gate_an_allow")
        names += ("requests_by_trigger", "requests_by_review")
        self.by = {name: Counter() for name in names}
        self.roots, self.told = set(), {"summary_embedding": set(), "summary_detector": set()}
        self.packets = set()

    def add(self, script, requests, calls):
        self.roots.add(script["root_id"])
        self.counts.update(views=1, events=len(script["events"]), proposed_calls=len(calls))
        self.counts.update(requests=len(requests))
        for call in calls:
            watch, gate = call["watch"], call["gate"]
            self.by["calls_by_level"][call["level"] or "unknown"] += 1
            self.counts["watch_triggered"] += bool(watch["triggered"])
            self.counts["watch_asked"] += watch["asked"]
            if watch["triggered"] and not watch["asked"]:
                self.by["watch_refused"][watch["why"]] += 1
            self.counts["gate_triggered"] += bool(gate["triggered"])
            self.by["gate"]["asked" if gate["asked"] else f"{gate['outcome']}:{gate['why']}"] += 1
            if gate["asked"]:
                self.by["gate_an_allow"][gate["an_allow"]] += 1
            self.counts["no_request_on_either_route"] += not gate["asked"] and not watch["asked"]
            self.counts["routes_disagree"] += call["routes_agree"] is False
        for row in requests:
            self.by["requests_by_trigger"][row["trigger"]["type"]] += 1
            self.by["requests_by_review"][row["review"]] += 1
            self.counts["requests_not_sendable"] += not row["sendable"]
            self.counts["requests_complete"] += bool(row["sendable"] and row["packet"]["complete"])
            self.counts["pre_action_complete"] += row["pre_action_complete"]
            self.packets.add(row["layout"]["packet"] or "")
            for key, seen in self.told.items():
                seen.add(json.dumps(row[key], sort_keys=True))

    def result(self):
        names = ("views", "events", "proposed_calls", "watch_triggered", "watch_asked")
        names += ("gate_triggered", "no_request_on_either_route", "routes_disagree", "requests")
        names += ("requests_not_sendable", "requests_complete", "pre_action_complete")
        out = {"roots": len(self.roots)} | {name: self.counts[name] for name in names}
        return out | {name: dict(sorted(count.items())) for name, count in self.by.items()}


def render(path, out, runtime, max_chars=5000, level="low", gate_level="medium"):
    """Render every script of a file into out and return the manifest.

    out must be a folder that is not there yet, or an empty one. The three files are written
    in a folder of this render's own beside it, and that folder takes out's place in one step
    when everything is done, so out holds a whole render or nothing, and two renders into the
    same place cannot mix: the second to finish is refused and leaves the first as it is.
    Raises ValueError, and leaves nothing behind, when the input is wrong, out is in use, or
    two renders of a view differ. Nothing that this render did not create is ever removed.
    """
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"{out} is already there and is not an empty folder")
    scripts, input_sha256 = read_scripts(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{out.name}.", suffix=".partial", dir=out.parent))
    tally, hashes, rows = Tally(), {}, Counter()
    try:
        with contextlib.ExitStack() as held:
            files = {}
            for name in (REQUESTS, CALLS):
                fd = os.open(work / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                files[name] = held.enter_context(os.fdopen(fd, "w", encoding="ascii"))
                hashes[name] = hashlib.sha256()
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
        fd = os.open(work / MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as file:
            file.write(json.dumps(manifest, indent=2) + "\n")
        try:
            # One step: a folder takes the place of an absent or empty one, and of no other.
            os.rename(work, out)
        except OSError:
            raise ValueError(
                f"{out} was written to while this render ran: it is left as it is"
            ) from None
    finally:
        shutil.rmtree(work, ignore_errors=True)  # this render's own folder, and nothing else
    return manifest
