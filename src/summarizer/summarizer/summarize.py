"""Incremental session state: typed units, their embeddings, observables, and the summary.

The summary is extractive. The embedding model ranks, links and deduplicates evidence; it never
writes text, so nothing in a summary can be authored by content the agent read. Entries and
observables are tagged with indicators (labels.py); the summary marks, it does not judge.

It is security memory for a detector that must decide about one action in its session context:
what was asked, what the agent encountered, what it attempted, what is known to have happened,
and what is unknown. An attempt is never recorded as an outcome, and what could not be captured
or scanned is reported rather than left out silently.
"""

import hashlib
import json
import re
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import MODEL, REVISION, SCHEMA, __version__, bundle
from .embedder import best_window
from .embedder import unit as normalize
from .events import (
    ACTION_CHARS,
    CHUNK_CHARS,
    ENTRY_IOCS,
    MAX_CHUNKS,
    TARGET_CHARS,
    Unit,
    agent_home,
    aside,
    chunk_spans,
    clip,
    describe_call,
    field_spans,
    output_blocks,
    output_source,
)
from .facts import (
    PERSISTENT,
    dotted_tokens,
    hidden_text,
    noted_sentences,
    observables,
    packages,
    path_classes,
    path_mentions,
    prompt_notes,
    text_flags,
    text_spans,
    wording_rules,
)
from .history import EFFECT_KINDS, MAX_EFFECT_PATHS, MAX_PATH_CHARS, call_effects, named_by_call
from .labels import (
    ACTION,
    COMPLETED,
    DRIFT_MARGIN,
    DRIFT_MAX,
    DRIFT_SLOPE,
    LOWER_TRUST,
    PROMPT_HIDDEN,
    RISKY,
    WEAK_TEXT,
    action_indicators,
    combine,
    content_indicators,
    ioc_indicators,
    priors,
)
from .probes import calibrated, fitted_for, probability, uncalibrated
from .probes import load as load_probes
from .text import MESSAGE_SHOWN, render

MIN_RANKED_CHARS = 32  # shorter fragments say too little to be ranked or tagged by resemblance
MIN_LINKED_CHARS = 16  # and a few characters resemble anything: no action is linked to them
SAME_AS_CHOSEN = 0.97  # as near as this to something chosen, a piece says what that says
DIVERSE_WEIGHT = 0.7  # of a piece's own score against its nearness to what is chosen
SEEN = "secret_in_output"  # a flow step that is a credential the hook redacted in tool output
MAX_RESULT_IDS = 64  # identities of a result's pieces listed on its action; the count is whole
# Later sightings of a piece that are listed on it: the first few and the newest. A call
# about to be decided turns on what was returned last, so those are never the ones dropped.
# The sightings in between are counted, and a line says how many it does not list.
MAX_SIGHTINGS, FIRST_SIGHTINGS = 16, 4
MAX_MESSAGES = 16  # earlier agent messages kept when they carry an indicator or an observable
MESSAGE_CHARS = 2000  # of an agent message kept; all of it is scanned
MAX_NOTED = 4  # excerpts kept around the wording that marked a long message
NOTED_CHARS = 240  # of each excerpt, as a packet shows it
MAX_OBSERVABLES = 4096  # tracked per session; later new ones are counted, not kept
# What each agent's hooks never deliver. A summary cannot know what happened on these surfaces.
NOT_CAPTURED = {
    "claude_code": [
        "files attached to a prompt with @",
        "MCP tool descriptions and schemas",
        "agent text between tool calls",
        "whether a prompt was typed, scheduled, a subagent report or from another session",
    ],
    "codex_code": [
        "hosted tools such as web search",
        "MCP tool descriptions and schemas",
        "agent text between tool calls",
        "instruction files other than AGENTS.md",
        "whether a command exited with an error (a result is returned either way)",
    ],
}


@dataclass(frozen=True)
class Config:
    max_chars: int = 3000  # budget of the text digest stored with each summary
    vector_dim: int = 256  # Matryoshka size of the exported session vectors
    max_marked: int = 128  # entries with indicators kept, most confident first
    max_iocs: int = 128  # observables with indicators kept, most confident first
    max_actions: int = 24  # unmarked actions kept for context: the latest, then the most distinct
    max_content: int = 12  # unmarked lower-trust excerpts kept for context
    max_prompts: int = 200  # user requests kept; they are the task, so nearly always all of them
    # Calls not kept as entries are catalogued in the session record (event, time, tool, status,
    # the call's clipped words), the newest this many, so that a lookup can account for every
    # recorded call it did not keep (MR198-1); how many more there are is said.
    max_dropped_calls: int = 1024


def effects_record(unit):
    """The effects recorded from a call's whole words, as a summary record carries them (MR199-1):
    {"effects": {kind: paths}}, up to MAX_EFFECT_PATHS paths in the order of EFFECT_KINDS, and
    when they are not whole, "effects_complete": false with why: how many paths are not listed
    (past the cap, or longer than any file system takes), whether the hook clipped the call's
    input, so that even its whole words are not all there, and whether a header of its patch
    was not read (MR200-1). A reader then knows that whether the call touched a file is not
    known."""
    told, room = {}, MAX_EFFECT_PATHS
    for kind in EFFECT_KINDS:
        paths = [path for path in unit.effects.get(kind) or () if len(path) <= MAX_PATH_CHARS]
        if paths and room > 0:
            told[kind] = list(paths[:room])
            room -= len(told[kind])
    record = {"effects": told}
    unlisted = sum(map(len, unit.effects.values())) - sum(map(len, told.values()))
    if unlisted or unit.input_clipped or unit.patch_unread:
        record["effects_complete"] = False
        if unlisted:
            record["effects_not_listed"] = unlisted
        if unit.input_clipped:
            record["input_clipped"] = True
        if unit.patch_unread:  # a header of its patch was not read: not every write is known
            record["patch_unread"] = True
    return record


def result_seq(unit):
    """The event that brought back what became of a call, its result or its failure, or None."""
    return next((seq for state, seq, _ in unit.lifecycle if state in ("returned", "failed")), None)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_identity(directory, module="summarize.py"):
    """A hash over the source files of a package directory, or None when there is no saying.

    None when a file cannot be read, and None when the files that are there do not include the
    module that is running: a package installed as compiled files only has no source to name,
    and a hash of nothing, or of other files, would look like an identity and be none.
    """
    try:
        found = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(directory).glob("*.py"))
        }
    except OSError:
        return None
    return _digest(found) if module in found else None


def detector():
    """What marks entries, by hash, so two summaries marked differently can be told apart.

    wording_sha256 covers the wording rules alone: their patterns, and which weak match yields
    to which rule. priors_sha256 covers every prior, the implied flags, the level thresholds and
    how indicators combine. Neither covers the rest: how commands, paths and observables are
    read, how text is cut into pieces, how a packet is laid out. source_sha256 does, as a hash
    of every source file of this package; it is None when the source is not there to be read.
    The embedding model and the probes are named beside this, under "embedding".
    """
    identity = {"version": 1, "package": __version__}
    identity |= {"wording_sha256": _digest(wording_rules()), "priors_sha256": _digest(priors())}
    return identity | {"source_sha256": source_identity(Path(__file__).resolve().parent)}


def operating():
    """The numbers the runtime applies to what an embedding model gives it, as plain data.

    They are thresholds on that model's similarities and sizes of the texts it is handed,
    so they hold for a model only as far as they were measured with it. A bundle's manifest
    lists the ones it is to be served under (bundle.py), and a bundle is not admitted under
    others. The same numbers may hold for several models; that is for a measurement to say.
    """
    return {
        "action_chars": ACTION_CHARS,
        "chunk_chars": CHUNK_CHARS,
        "min_ranked_chars": MIN_RANKED_CHARS,
        "min_linked_chars": MIN_LINKED_CHARS,
        "drift": {"margin": DRIFT_MARGIN, "slope": DRIFT_SLOPE, "max": DRIFT_MAX},
        "diversity": {"same": SAME_AS_CHOSEN, "weight": DIVERSE_WEIGHT},
    }


class Runtime:
    """An embedder and the probe vectors computed with it, shared by every session."""

    def __init__(self, embedder, probes=None, config=None, strict=False):
        self.embedder = embedder
        self.detector = detector()
        self.config = config or Config()
        self.strict = strict  # raise on an event that cannot be read instead of counting it
        self.probe_version, self.probes = load_probes(probes)
        info = embedder.info
        # Why the probes' calibration does not hold for this encoder, when it does not. The
        # numbers are then dropped and no probe label is given: marks made with a threshold
        # that was fitted for something else would look like marks and mean nothing.
        self.uncalibrated_why = None
        # A model directory is admitted when it, its probes and these numbers are what its
        # manifest lists. One that is not has nothing binding its probes to its weights.
        listed, refused = getattr(embedder, "bundle", None), getattr(embedder, "unadmitted", None)
        if listed is not None:
            mine = {"probes": self.probe_version, "operating": operating()}
            refused = bundle.unlike(listed, mine)
        self.bundle = None if refused or listed is None else bundle.name(listed)
        if refused:
            self.uncalibrated_why = f"the model bundle is not admitted: {refused}"
        elif probes is None and (info.get("model"), info.get("revision")) != (MODEL, REVISION):
            self.uncalibrated_why = "the built-in probe numbers fit the pinned base model only"
        else:
            self.uncalibrated_why = next(
                filter(None, (fitted_for(probe, info) for probe in self.probes)), None
            )
        if self.uncalibrated_why:
            self.probes = uncalibrated(self.probes)
        rows = [embedder.queries(probe["queries"]).mean(axis=0) for probe in self.probes]
        self.probe_vectors = normalize(np.stack(rows))
        self.calibrated = any(calibrated(probe) for probe in self.probes)

    def identity(self):
        """What a summary made with this runtime says made it: the encoder with its probes and
        whether they are calibrated, and what marks entries. Two summaries with different
        identities were not made the same way: their marks cannot be compared, and their
        vectors are not of one space."""
        embedding = dict(self.embedder.info)
        embedding |= {"probes": self.probe_version, "calibrated": self.calibrated}
        return {"embedding": embedding, "detector": self.detector}

    def matches(self, scores):
        """Calibrated probes a piece of content matches: (label, confidence, id, probability)."""
        found = []
        for probe, score in zip(self.probes, scores, strict=True):
            if not calibrated(probe) or score < probe.get("threshold", -1.0):
                continue
            chance = probability(probe, float(score))
            found.append((probe["label"], probe["weight"] * chance, probe["id"], chance))
        return found


def load(model=MODEL, probes=None, backend="embeddinggemma", config=None, **options):
    """The runtime for a model, as every server of it should make it: one way to find the
    probes, and one place where what cannot be relied on is said.

    model is the pinned base, another hub id, or a model directory. probes is a probes
    file or folder; when it is not given, a model directory's own probes.jsonl is used, and
    otherwise the built-in set. options go to the embedder (revision, device,
    local_files_only). backend "hash" is the stand-in for tests and dry runs.

    The runtime says what it found: calibrated or not, and why not (uncalibrated_why): the
    built-in numbers on a model they were not fitted for, a calibration made under another
    profile, a directory its manifest does not admit. Whether to serve a runtime that
    gives no probe label is its caller's decision, and must be a decision: a server that
    quietly marks less looks like one that found nothing to mark.
    """
    from .embedder import create

    if backend == "hash":
        return Runtime(create("hash"), config=config)
    if probes is None and (Path(model) / "probes.jsonl").is_file():
        probes = model  # a fine-tuned model directory carries its calibrated probes
    return Runtime(create(backend, model=model, **options), probes, config)


class Session:
    def __init__(self, agent, session_id, runtime):
        self.agent, self.session_id, self.runtime = agent, session_id, runtime
        self.home, self._home_known = str(Path.home()), False
        self.units = []
        self.vectors = np.zeros((64, runtime.embedder.dim), dtype=np.float32)
        self.embedded = 0  # units[:embedded] have a row in vectors
        self.events = 0
        self.log_bytes = 0  # bytes of the event log consumed; set by whoever feeds add()
        self.info = {}
        self.counts = Counter()
        self.unhandled = Counter()  # hook events this version does not interpret, by name
        self.prompts = []
        self.context = []  # session-shaping events: resumes, compactions, subagents, config
        self.sources = {}  # (source, origin) of lower-trust input -> first seq, chunk count
        self.iocs = {}  # (kind, value) -> where an observable was named and how it was used
        self.calls = {}  # tool_use_id -> index of its action unit
        self.outputs = {}  # action index -> (trust, source, origin) of what the call returns
        self.recent_calls = deque(maxlen=32)
        self.hashes = {}  # digest of (trust, text) -> index of the unit that holds that content
        # call id -> [(unit index, whether it arrived as schema)]: every piece every call
        # returned, first sighting or not. Kept whole: the sightings listed on a piece are
        # capped for display, and what a call returned must not depend on that cap.
        self.returned_by = {}
        self.unlisted = {}  # unit index -> sightings of it beyond those listed on it
        self.seen = {}  # dotted token -> index of the first lower-trust unit that carried it
        self.user_tokens = {}  # dotted token -> seq of the first user request that carried it
        self.user_paths = {}  # sensitive or configuration path -> seq of the request that named it
        self.origins = {}  # action index -> lower-trust unit that first named what it used
        self.scores = {}  # lower-trust unit index -> probe similarities
        self.spans = {}  # lower-trust unit index -> the windows its score is the best of
        self.links = {}  # action index -> ((request sim, index), (content sim, index))
        # Units the model could not embed: as a document, and as the query an action is. Such
        # a unit has no vector. It is compared with nothing and nothing is compared with it.
        self.unembedded, self.unqueried = set(), set()
        # Units whose text was longer than the encoder's window: embedded as several windows
        # that hold all of it (windowed), or as far as the windows reach and no further
        # (partly), which is a loss and is said in the coverage.
        self.windowed, self.partly = set(), set()
        # Actions that could not be compared with every earlier user request: nothing can be
        # said about what they resemble "more than any user request".
        self.incomparable = set()
        self.messages = []  # what the agent said each time it stopped
        self._handlers = {
            "SessionStart": self._session_start,
            "SessionEnd": self._session_end,
            "UserPromptSubmit": self._prompt,
            "PreToolUse": self._pre_tool,
            "PostToolUse": self._post_tool,
            "PostToolUseFailure": self._post_tool,
            "PermissionRequest": self._permission_request,
            "PermissionDenied": self._permission_denied,
            "Stop": self._stop,
            "SubagentStart": self._subagent_start,
            "SubagentStop": self._subagent_stop,
            "PostCompact": self._post_compact,
            "InstructionsLoaded": self._instructions,
            "ConfigChange": self._config_change,
        }

    # --- ingest --------------------------------------------------------------------------------

    def add(self, record):
        """Consume one line of the event log. An unreadable event is counted, never fatal."""
        self.events += 1
        try:
            self._consume(record)
        except Exception:
            if self.runtime.strict:
                raise
            self.counts["corrupt_events"] += 1

    def _consume(self, record):
        event = record.get("event")
        if record.get("corrupt") or not isinstance(event, dict):
            self.counts["corrupt_events"] += 1
            return
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        ts = record.get("ts") if isinstance(record.get("ts"), int | float) else 0.0
        for note in meta.get("clipped", ()):
            self.counts["clipped_chars"] += max(0, note.get("chars", 0) - note.get("kept", 0))
        for note in meta.get("redacted", ()):
            self.counts["redactions"] += sum(note.get("kinds", {}).values())
        self._track(self.events, event, ts)
        name = str(event.get("hook_event_name"))
        handler = self._handlers.get(name)
        if handler:
            handler(self.events, ts, event, meta)
        else:
            self.unhandled[name] += 1
        for item in meta.get("files", ()):  # instruction files the hook read for this event
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            if isinstance(item.get("content"), str):
                blocks = [(f"file:{path}", item["content"])]
                redacted = {f"file:{path}"} if item.get("redacted") else ()
                self._content(self.events, ts, blocks, "untrusted", "instructions", path, redacted)
                self.counts["clipped_chars"] += int(item.get("clipped") or 0)
            else:  # the agent may have read it; the hook could not
                self.counts["unreadable_files"] += 1
                self._note(self.events, "instructions_unreadable", path, str(item.get("error")))

    def _track(self, seq, event, ts):
        info = self.info
        info.setdefault("started_at", ts)
        if isinstance(event.get("session_id"), str):
            self.session_id = event["session_id"]  # the directory name may be a hash of it
        for key in ("cwd", "transcript_path", "model"):
            if isinstance(event.get(key), str) and event[key]:
                info.setdefault(key, event[key])
        if not self._home_known and (found := agent_home(info.get("transcript_path"))):
            self.home, self._home_known = found, True
        mode = event.get("permission_mode")
        if isinstance(mode, str) and mode != info.get("permission_mode"):
            if "permission_mode" in info or mode == "bypassPermissions":
                self._note(seq, "permission_mode", mode)  # a change, or a session without prompts
            info["permission_mode"] = mode

    def _note(self, seq, kind, detail="", note=""):
        item = {"seq": seq, "kind": kind}
        if detail:
            item["detail"] = clip(str(detail), 240)
        if note:
            item["note"] = note
        self.context.append(item)

    def _cwd(self, event):
        cwd = event.get("cwd")
        return cwd if isinstance(cwd, str) and cwd else self.info.get("cwd", "")

    def _append(self, unit):
        self.units.append(unit)
        return len(self.units) - 1

    def _observe(self, kind, value, seq, trust, unit=None):
        """Record one sighting of an observable: who named it, and when each party first did."""
        seen = self.iocs.get((kind, value))
        if seen is None:
            if len(self.iocs) >= MAX_OBSERVABLES:
                self.counts["observables_dropped"] += 1
                return None
            seen = {"first": seq, "by": trust, "unit": unit, "user": None, "count": 0}
            seen |= {"seqs": [], "uses": [], "mentions": []}
            self.iocs[(kind, value)] = seen
        seen["count"] += 1
        if trust == "user" and seen["user"] is None:
            seen["user"] = seq
        if len(seen["seqs"]) < 8 and seq not in seen["seqs"]:
            seen["seqs"].append(seq)
        if trust not in {mention["by"] for mention in seen["mentions"]}:
            seen["mentions"].append({"seq": seq, "by": trust})
        return seen

    def _use(self, kind, value, action, seq, roles):
        """Record that an agent action named an observable, and what it meant to do with it."""
        seen = self._observe(kind, value, seq, "agent")
        if seen is None:
            return False
        for role in roles:
            if len(seen["uses"]) < 64:
                seen["uses"].append({"unit": action, "role": role})
        self.units[action].iocs.append((kind, value))
        return True

    def _content(self, seq, ts, blocks, trust, source, origin, redacted=(), **origin_call):
        """File lower-trust text as chunk units; blocks are (event field, text).

        Wording that steers an agent, paths worth protecting and observables are found on the
        whole text, so nothing is missed where a chunk happens to end. Content seen before is
        kept once, and each later sighting is recorded on it.
        """
        entry = self.sources.setdefault(
            (source, origin), {"seq": seq, "source": source, "origin": origin, "chunks": 0}
        )
        entry["last"] = seq
        budget, cwd = MAX_CHUNKS, self.info.get("cwd", "")
        for field, text in blocks:
            spans, unscanned = field_spans(field, text, limit=budget)
            self.counts["unscanned_chars"] += unscanned
            budget -= len(spans)
            marks = sorted(text_flags(text) + path_mentions(text, cwd, self.home))
            named = sorted(observables(text))
            scrubbed = any(path.startswith(field) or field.startswith(path) for path in redacted)
            for start, end in spans:
                piece = text[start:end].strip()
                entry["chunks"] += 1
                key = f"{trust}\0{piece}".encode("utf-8", "surrogatepass")
                digest = hashlib.blake2b(key, digest_size=12).digest()
                call = origin_call.get("call_id", "")
                # Whether it arrived as schema is said of each sighting, by the field it sat
                # in there: the same words can be a member name in one result and what a
                # later call printed.
                schema = aside(origin_call.get("tool"), field)
                if digest in self.hashes:
                    self.counts["duplicate_chunks"] += 1
                    held = self.hashes[digest]
                    again = self.units[held].occurrences
                    again.append((seq, source, origin, call, field, start, end, schema))
                    if len(again) > MAX_SIGHTINGS:  # the oldest of the later ones gives way
                        del again[FIRST_SIGHTINGS]
                        self.unlisted[held] = self.unlisted.get(held, 0) + 1
                    if call:
                        self.returned_by.setdefault(call, []).append((held, schema))
                    continue
                unit = Unit(seq, ts, "output", trust, source, origin, piece, **origin_call)
                unit.where, unit.span = field, (start, end)
                index = self.hashes[digest] = self._append(unit)
                if call:
                    self.returned_by.setdefault(call, []).append((index, schema))
                if hidden_text(piece):
                    unit.flags["hidden_text"] = "invisible or direction-changing characters"
                if scrubbed and "[REDACTED:" in piece:
                    unit.flags["secret_material"] = "credential format redacted by the hook"
                for at, flag, basis in marks:
                    if start <= at < end:
                        unit.flags.setdefault(flag, basis)
                        if flag.endswith("_reference"):  # a path: an observable too
                            path = self._observe("path", basis, seq, trust, index)
                            if path is not None:
                                path.setdefault("referenced", flag)
                                unit.iocs.append(("path", basis))
                for at, kind, value in named:
                    if start <= at < end and self._observe(kind, value, seq, trust, index):
                        unit.iocs.append((kind, value))
                for token in dotted_tokens(piece):
                    self.seen.setdefault(token, index)

    def _first_mention(self, name):
        """The lower-trust unit that first named a word, unless the user named it too."""
        word = re.compile(rf"(?<![\w.-]){re.escape(name)}(?![\w-])")
        if any(word.search(prompt["text"]) for prompt in self.prompts):
            return None
        for index, unit in enumerate(self.units):
            if unit.trust in LOWER_TRUST and word.search(unit.text):
                return index
        return None

    def _action(self, seq, ts, event, meta, proposal_seen=True):
        """Record a tool call the agent proposed. What becomes of it arrives in later events.

        proposal_seen is False when the event at hand is not the proposal but something later
        that reveals the call (its result, its failure, its refusal). The call is recorded all
        the same, without a "proposed" step: when it was proposed, and what the session held
        at that moment, were not observed and are not inferred.
        """
        tool = str(event.get("tool_name") or "")
        real = {
            item["path"]: item["real"]
            for item in meta.get("paths", ())
            if isinstance(item, dict) and isinstance(item.get("real"), str)
        }
        cwd = self._cwd(event)
        root = self.info.get("cwd", cwd)
        call = describe_call(tool, event.get("tool_input"), cwd, self.home, real, root)
        if "analysis_partial" in call["flags"]:
            self.counts["partial_commands"] += 1
        call_id = str(event.get("tool_use_id") or "")
        flags = dict(call["flags"])
        unit = Unit(seq, ts, "action", "agent", "agent", call["target"], call["text"])
        unit.tool, unit.call_id, unit.status, unit.flags = tool, call_id, "proposed", flags
        unit.lifecycle = [("proposed", seq, ts)] if proposal_seen else []
        if not proposal_seen:
            unit.proposal_seen = False
            unit.notes.append(f"its proposal was not observed; first seen in #{seq}")
            self.counts["proposals_not_observed"] += 1
        unit.hosts, unit.where, unit.chars = set(call["hosts"]), "tool_input", call["chars"]
        unit.call_sha256 = str(meta.get("call_sha256") or "")
        # What the call's whole words show it doing to files, read now, before anything is clipped
        # for display, so that an object's account never reads a clipped call as whole (MR199-1);
        # and whether the hook clipped its input, which leaves even these not whole.
        unit.effects = call_effects(
            tool, call["target"], call["whole"], cwd, self.home, writes=call["writes"]
        )
        unit.patch_unread = not call["writes_whole"]
        whole = {"tool": tool, "target": call["target"], "text": call["whole"]}
        unit.named = sorted(named_by_call(whole, cwd, self.home))
        unit.input_clipped = any(
            str(note.get("path", "")).partition(".")[0].partition("[")[0] == "tool_input"
            for note in meta.get("clipped", ())
            if isinstance(note, dict)
        )
        if event.get("agent_id"):
            unit.agent_id = str(event["agent_id"])
            unit.actor = str(event.get("agent_type") or "")
        index = self._append(unit)
        self.outputs[index] = output_source(tool, event.get("tool_input"), call)
        if call_id:
            self.calls[call_id] = index
            self.recent_calls.append(call_id)

        body = call["body"]
        # The body is scanned for steering wording when the agent composed it; the other input
        # fields (a description, the text being replaced) always are.
        prose = (body if call["prose"] else "") + "\n" + call["extra"]
        found = text_flags(prose)
        if wording := sorted({basis for _, _, basis in found}):
            strong = any(flag not in WEAK_TEXT for _, flag, _ in found)
            name = "steering_text" if strong else "weak_steering_text"
            flags[name] = clip("; ".join(wording), 160)
        external = bool(flags.keys() & {"network", "mcp"})
        roles = ("agent",) + ("contacted",) * external
        roles += ("sent",) * bool(flags.keys() & {"network_send", "mcp_write"})
        # What the tool is given to act on is used; what the agent only wrote about the call (a
        # description, the text an edit replaces) is a mention by the agent, never an operand.
        named = {(kind, value) for _, kind, value in observables(body)}
        named |= {("domain", host) for host in call["hosts"] if ("ip", host) not in named}
        beside = {(kind, value) for _, kind, value in observables(call["extra"])} - named
        for kind, value in sorted(named | beside):
            used = (kind, value) in named
            origin = self.seen.get(value) if kind in ("domain", "ip") else None
            asked = self.user_tokens.get(value)
            if asked is not None and kind in ("domain", "ip"):
                unit.notes.append(f"{value} was named by the user in #{asked}")
            elif origin is not None:
                # Only lower-trust content has named this destination so far.
                source = self.units[origin]
                flag = "untrusted_target" if external and used else "untrusted_reference"
                flags.setdefault(flag, f"{value} first named by lower-trust #{source.seq}")
                self.origins.setdefault(index, origin)
                if (kind, value) not in self.iocs:
                    self._observe(kind, value, source.seq, source.trust, origin)
            if used:
                self._use(kind, value, index, seq, roles)
            elif self._observe(kind, value, seq, "agent") is not None:
                unit.iocs.append((kind, value))
        for path, classes in call["paths"].items():
            roles = ("read",) * ("sensitive" in classes)
            roles += ("written",) * bool(classes & PERSISTENT and "persistence_write" in flags)
            if roles and self._use("path", path, index, seq, roles) and path in self.user_paths:
                unit.notes.append(f"{path} was named by the user in #{self.user_paths[path]}")
        for package in sorted(packages(body)) if "package_install" in flags else ():
            origin = self._first_mention(package.partition(":")[2])
            if origin is not None:
                source = self.units[origin]
                flags.setdefault(
                    "untrusted_package", f"{package} first named by lower-trust #{source.seq}"
                )
                self.origins.setdefault(index, origin)
                if ("package", package) not in self.iocs:
                    self._observe("package", package, source.seq, source.trust, origin)
            self._use("package", package, index, seq, ("installed",))
        return index

    def _called(self, seq, ts, event, meta):
        """The action a later event is about; recorded now if its proposal was never seen."""
        index = self.calls.get(str(event.get("tool_use_id") or ""))
        return self._action(seq, ts, event, meta, proposal_seen=False) if index is None else index

    def _session_start(self, seq, ts, event, meta):
        source = str(event.get("source") or "startup")
        self.info.setdefault("source", source)
        self.counts["session_starts"] += 1
        if source != "startup":
            self._note(seq, "session", source)
        for item in meta.get("files", ()):  # instruction files Codex loads at start
            if isinstance(item, dict) and "content" in item:
                self._note(seq, "instructions", item.get("path") or "", "session_start")

    def _session_end(self, seq, ts, event, meta):
        self.info["ended"] = str(event.get("reason") or "other")

    def _user_text(self, seq, ts, text, channel, question=""):
        """Text that reached the agent on a user channel: the channel is known, its author not.

        question is what the agent asked when the text is an answer given in a dialog. The agent
        wrote it, so nothing it names counts as named by the user; it is kept beside the answer.
        """
        prompt = {"seq": seq, "ts": ts, "text": clip(text, 4000), "chars": len(text)}
        prompt |= {"hidden": hidden_text(text), "notes": prompt_notes(text), "channel": channel}
        if channel == "question_answer":  # an explicit decision, however short: never dropped
            prompt["notes"] = ["answer", *prompt["notes"]]
            prompt["question"] = clip(question, 400)
        prompt["noted"] = [clip(s, 240) for s in noted_sentences(text)] if len(text) > 240 else []
        self.prompts.append(prompt)
        for token in dotted_tokens(text):
            self.user_tokens.setdefault(token, seq)
        named = [(kind, value) for _, kind, value in sorted(observables(text))]
        for _, _, path in path_mentions(text, self.info.get("cwd", ""), self.home):
            self.user_paths.setdefault(path, seq)
            named.append(("path", path))
        prompt["iocs"] = [
            pair for pair in dict.fromkeys(named) if self._observe(*pair, seq, "user")
        ]
        spans, unscanned = chunk_spans(text)
        self.counts["unscanned_chars"] += unscanned
        for start, end in spans:
            unit = Unit(seq, ts, "prompt", "user", "user", "", text[start:end].strip())
            unit.where, unit.span = channel, (start, end)
            self._append(unit)

    def _prompt(self, seq, ts, event, meta):
        text = event.get("prompt")
        if isinstance(text, str) and text.strip():
            self._user_text(seq, ts, text, "prompt")

    def _pre_tool(self, seq, ts, event, meta):
        if str(event.get("tool_use_id") or "") not in self.calls:
            self._action(seq, ts, event, meta)

    def _post_tool(self, seq, ts, event, meta):
        failed = event.get("hook_event_name") == "PostToolUseFailure"
        index = self._called(seq, ts, event, meta)
        action = self.units[index]
        action.status = "failed" if failed else COMPLETED
        action.lifecycle.append((action.status, seq, ts))
        response = event.get("tool_response")
        if failed:
            error = event.get("error")
            blocks = [("error", error)] if isinstance(error, str) and error.strip() else []
        else:
            if action.tool == "AskUserQuestion" and isinstance(response, dict):
                # The answers are the user's, given through a dialog rather than the prompt box.
                # Everything else in the response is a tool result like any other.
                answers = response.get("answers")
                if isinstance(answers, dict) and all(isinstance(a, str) for a in answers.values()):
                    for question, answer in answers.items():
                        if answer.strip():
                            self._user_text(seq, ts, answer, "question_answer", str(question))
                    response = {key: item for key, item in response.items() if key != "answers"}
            call = describe_call(action.tool, event.get("tool_input"), self._cwd(event), self.home)
            covered = f"{call['target']}\n{call['body']}\n{call['extra']}\n{action.origin}"
            blocks, echoes = output_blocks(
                action.tool, event.get("tool_input"), response, scanned=covered
            )
            self.counts["echoed_fields"] += echoes
            if action.tool == "ExitPlanMode":
                self._note(seq, "plan_approved", action.origin)
        if blocks:
            redacted = {str(note.get("path", "")) for note in meta.get("redacted", ())}
            trust, source, origin = self.outputs[index]
            call = {"tool": action.tool, "call_id": action.call_id, "actor": action.actor}
            self._content(seq, ts, blocks, trust, source, origin, redacted, **call)

    def _permission_request(self, seq, ts, event, meta):
        tool = str(event.get("tool_name") or "")
        text = describe_call(tool, event.get("tool_input"), self._cwd(event), self.home)["text"]
        pending = [u for u in self.units[-32:] if u.kind == "action" and u.status == "proposed"]
        match = next((u for u in reversed(pending) if u.tool == tool and u.text == text), None)
        if match is None:
            match = self.units[self._action(seq, ts, event, meta)]
        match.lifecycle.append(("approval_requested", seq, ts))
        match.notes.append(f"approval was requested in #{seq}")

    def _permission_denied(self, seq, ts, event, meta):
        action = self.units[self._called(seq, ts, event, meta)]
        action.status = "denied"
        action.lifecycle.append(("denied", seq, ts))
        action.flags["denied"] = clip(str(event.get("reason") or ""), 120)

    def _stop(self, seq, ts, event, meta):
        text = event.get("last_assistant_message")
        if isinstance(text, str) and text.strip():
            self.messages.append(self._message(seq, ts, text))

    def _message(self, seq, ts, text):
        """What the agent said when it stopped: scanned whole, kept at its head and tail.

        It is the agent's own prose. Wording in it is an indicator on the agent, never a
        request by the user, and a URL in it is something the agent wrote, not a contact made.
        """
        found = text_flags(text)
        indicators = []
        if found:
            wording = sorted({basis for _, _, basis in found})
            strong = any(flag not in WEAK_TEXT for _, flag, _ in found)
            confidence = ACTION["steering_text" if strong else "weak_steering_text"][1]
            basis = clip("; ".join(wording), 160)
            indicators.append({"label": "steering_text", "confidence": confidence, "basis": basis})
        if hidden_text(text):
            basis = "invisible or direction-changing characters"
            indicators.append({"label": "hidden_text", "confidence": PROMPT_HIDDEN, "basis": basis})
        named = sorted({(kind, value) for _, kind, value in observables(text)})
        for kind, value in named:
            self._observe(kind, value, seq, "agent")
        message = {"seq": seq, "ts": ts, "text": text, "indicators": indicators}
        if named:
            message["iocs"] = [f"{kind}:{value}" for kind, value in named][:ENTRY_IOCS]
        if len(text) > MESSAGE_SHOWN and found:
            # A packet shows the start of a message. What raised an indicator further in is
            # kept beside it: first one place for each kind of wording, then the rest in order,
            # as many as there is room for. Coverage is measured on the source span of each
            # match against what is really displayed (a quoted piece loses its last character
            # to the cut mark). A span that is not displayed whole is counted, never implied.
            spans = sorted(text_spans(text))
            head, room = MESSAGE_SHOWN - 1, NOTED_CHARS - 1
            windows = []

            def shown(start, end):
                return end <= head or any(low <= start and end <= high for low, high in windows)

            first = {}
            for start, end, flag, _ in spans:
                first.setdefault(flag, (start, end))
            for start, end in [*sorted(first.values()), *((a, b) for a, b, _, _ in spans)]:
                if not shown(start, end) and len(windows) < MAX_NOTED:
                    low = max(0, start - 60) if end - max(0, start - 60) <= room else start
                    windows.append((low, min(len(text), low + room)))
            missed = sum(1 for start, end, _, _ in spans if not shown(start, end))
            message["noted"] = [text[low:high] for low, high in sorted(windows)]
            if missed:
                message["noted_missed"] = missed
        if len(text) > MESSAGE_CHARS:  # the head and the tail are kept
            head = MESSAGE_CHARS * 2 // 3
            message["text"] = f"{text[:head]} … {text[-(MESSAGE_CHARS - head) :]}"
            message["text_chars"] = len(text)
        return message

    def _subagent_start(self, seq, ts, event, meta):
        detail = str(event.get("agent_type") or "")
        self._note(seq, "subagent", detail, str(event.get("agent_id") or ""))

    def _subagent_stop(self, seq, ts, event, meta):
        text = event.get("last_assistant_message")
        if isinstance(text, str) and text.strip():
            actor = str(event.get("agent_type") or "")
            blocks = [("last_assistant_message", text)]
            self._content(seq, ts, blocks, "derived", "subagent", actor, actor=actor)

    def _post_compact(self, seq, ts, event, meta):
        trigger = str(event.get("trigger") or "")
        self._note(seq, "compaction", trigger)
        text = event.get("compact_summary")
        if isinstance(text, str) and text.strip():
            self._content(seq, ts, [("compact_summary", text)], "derived", "compaction", trigger)

    def _instructions(self, seq, ts, event, meta):
        path = str(event.get("file_path") or "")
        classes = path_classes(path, self.info.get("cwd", ""), self.home)
        expected = "outside" not in classes or path.startswith(self.home + "/.claude/")
        note = str(event.get("load_reason") or "") + ("" if expected else ",outside_workspace")
        self._note(seq, "instructions", path, note)

    def _config_change(self, seq, ts, event, meta):
        self._note(
            seq, "config_change", event.get("file_path") or "", str(event.get("source") or "")
        )

    # --- embed ---------------------------------------------------------------------------------

    def _embed(self, how, units):
        """(vectors for units' text, the rows that have none, {row: the vectors of the
        windows of a text embedded as several}, the rows of which some part was embedded in
        no window). A failure is never fatal.

        A daemon that raised here would leave the session without a summary from then on,
        and whatever the agent read next would go unseen. So text the model cannot take is
        set aside: its row is reported as failed and must not be used as a vector. A row of
        zeros is not "no resemblance": against a negative similarity it would win. The
        caller keeps such a unit out of every comparison. The wording rules and the facts
        read off its text are unaffected, and the summary says in its coverage that it has
        such a unit.
        """
        embedder = self.runtime.embedder
        encode = getattr(embedder, how)
        texts = [unit.text for unit in units]

        def beyond(row=0):
            # What the embedder says of its last call: the windows of a text it embedded
            # as several, and whether some of a text is in no window.
            parts = {row + at: part for at, part in getattr(embedder, "parts", {}).items()}
            return parts, {row + at for at in getattr(embedder, "cut", ())}

        try:
            return encode(texts), set(), *beyond()
        except Exception:
            if self.runtime.strict:
                raise
        vectors, failed = np.zeros((len(texts), self.vectors.shape[1]), np.float32), set()
        parts, cut = {}, set()
        for row, text in enumerate(texts):  # one at a time: the others are still embedded
            try:
                vectors[row] = encode([text])[0]
            except Exception:
                failed.add(row)
                continue
            more, short = beyond(row)
            parts |= more
            cut |= short
        return vectors, failed, parts, cut

    def flush(self):
        """Embed units added since the last flush, score new content and link new actions."""
        start, end = self.embedded, len(self.units)
        if start == end:
            return
        if end > len(self.vectors):
            grown = np.zeros((max(end, 2 * len(self.vectors)), self.vectors.shape[1]), np.float32)
            grown[:start] = self.vectors[:start]
            self.vectors = grown
        self.vectors[start:end], failed, parts, cut = self._embed(
            "documents", self.units[start:end]
        )
        self.unembedded |= {start + row for row in failed}
        self.windowed |= {start + row for row in parts}
        self.partly |= {start + row for row in cut}
        self.embedded = end
        probes = self.runtime.probe_vectors
        for index in range(start, end):
            if self.units[index].trust in LOWER_TRUST and index not in self.unembedded:
                # A text embedded as several windows is scored by its best window. How many
                # there were is kept: a mark made on the best of several says so.
                pieces = parts.get(index - start)
                self.scores[index] = best_window(
                    lambda vectors: vectors @ probes.T, self.vectors[index], pieces
                )
                if pieces is not None:
                    self.spans[index] = len(pieces)
        self.counts["windowed"] = len(self.windowed)
        self.counts["embedded_in_part"] = len(self.partly)
        actions = [index for index in range(start, end) if self.units[index].kind == "action"]
        if not actions:
            self.counts["unembedded"] = len(self.unembedded | self.unqueried)
            return
        kinds = np.array([unit.kind for unit in self.units[:end]])
        trust = np.array([unit.trust for unit in self.units[:end]])
        prompts = np.flatnonzero(kinds == "prompt")
        sized = np.array([len(unit.text) >= MIN_LINKED_CHARS for unit in self.units[:end]])
        content = np.flatnonzero(np.isin(trust, LOWER_TRUST) & sized)
        queries, failed, parts, cut = self._embed(
            "queries", [self.units[index] for index in actions]
        )
        self.unqueried |= {actions[row] for row in failed}
        self.windowed |= {actions[row] for row in parts}
        self.partly |= {actions[row] for row in cut}
        self.counts["windowed"] = len(self.windowed)
        self.counts["embedded_in_part"] = len(self.partly)
        self.counts["unembedded"] = len(self.unembedded | self.unqueried)
        without = np.array(sorted(self.unembedded), dtype=np.int64)

        def nearest(pool, query):
            pool = pool[~np.isin(pool, without)]  # only what has a vector can be near anything
            if not len(pool):
                return None
            similarity = self.vectors[pool] @ query
            best = int(similarity.argmax())
            return float(similarity[best]), int(pool[best])

        for index, query in zip(actions, queries, strict=True):
            # Only what the agent had already been given can have caused an action. These are
            # candidate links for a reader to weigh, not findings about cause.
            asked, read = prompts[prompts < index], content[content < index]
            if index in self.unqueried:  # the action itself has no vector to compare
                self.links[index] = (None, None)
                continue
            self.links[index] = (nearest(asked, query), nearest(read, query))
            if np.isin(asked, without).any():
                self.incomparable.add(index)

    def _unseen(self, pool, room):
        """Those of pool the model could not embed that a choice by recency alone would keep.

        Nothing is known about what such a unit resembles, so it is neither preferred as
        new nor passed over as familiar. It is kept exactly when it is among the newest
        `room` of the pool, which is what would be chosen with no model at all. The rest
        of the room goes to _diverse(), which never sees it.
        """
        newest = sorted(pool)[-room:] if room > 0 else []
        return [index for index in newest if index in self.unembedded]

    def _diverse(self, pool, scores, count, anchors=(), weight=DIVERSE_WEIGHT):
        """Pick up to count of pool by score, discounting similarity to what is already chosen.

        Only units that have a vector take part. The row of one the model could not embed
        is a placeholder: read as a vector it would be unlike everything, and so the first
        to be picked. _unseen() decides about those.
        """
        pool = [i for i in pool if i < self.embedded and i not in self.unembedded]
        if count <= 0 or not pool:
            return []
        vectors = self.vectors[pool]
        value = np.asarray([scores.get(index, 0.0) for index in pool], dtype=np.float32)
        anchors = [i for i in anchors if i < self.embedded and i not in self.unembedded]
        near = (vectors @ self.vectors[anchors].T).max(axis=1) if anchors else np.zeros(len(pool))
        available = np.ones(len(pool), dtype=bool)
        picked = []
        while len(picked) < count and available.any():
            merit = np.where(available, weight * value - (1 - weight) * near, -np.inf)
            best = int(merit.argmax())
            available[best] = False
            if near[best] > SAME_AS_CHOSEN:  # says what something already chosen says
                continue
            picked.append(pool[best])
            near = np.maximum(near, vectors @ vectors[best])
        return picked

    # --- tag -----------------------------------------------------------------------------------

    def _flow(self, actions, content):
        """Whether untrusted input, sensitive access and outside contact co-occur in this session.

        It is a session-wide pattern, not evidence that particular data moved. "observed" needs a
        sensitive access and a later outside contact that both returned a result; attempts alone
        make it "potential". Every candidate step keeps its own status.
        """
        units = self.units
        untrusted = min((entry["seq"] for entry in self.sources.values()), default=None)
        touched = [
            (units[i].seq, units[i].status) for i in actions if "sensitive" in units[i].flags
        ]
        touched += [(units[i].seq, SEEN) for i in content if "secret_material" in units[i].flags]
        touched.sort()
        outside = [
            (units[i].seq, units[i].status)
            for i in actions
            if units[i].flags.keys() & {"network", "mcp"}
        ]
        known = [seq for seq, status in touched if status in (COMPLETED, SEEN)]
        state = "none"
        if untrusted is not None and touched and outside:
            first = min(seq for seq, _ in touched)
            if any(seq > max(untrusted, first) for seq, _ in outside):
                state = "potential"
            done = [seq for seq, status in outside if status == COMPLETED]
            if known and any(seq > max(untrusted, min(known)) for seq in done):
                state = "observed"
        return {
            "state": state,
            "untrusted_input": untrusted,
            "sensitive_access": [{"seq": seq, "status": status} for seq, status in touched[:8]],
            "external_contact": [{"seq": seq, "status": status} for seq, status in outside[-8:]],
            "observed_sensitive": min(known, default=None),
        }

    def _indicators(self, actions, content, flow):
        """Indicators for every action and lower-trust unit, keyed by unit index."""
        units, found = self.units, {}
        untrusted, touched = flow["untrusted_input"], flow["sensitive_access"]
        named, followed, risky = {}, {}, {}
        for index in actions:
            unit = units[index]
            after, link = None, None
            if untrusted is not None and touched and unit.flags.keys() & {"network", "mcp"}:
                known = flow["observed_sensitive"]
                first = known if known is not None and known < unit.seq else touched[0]["seq"]
                if unit.seq > max(untrusted, first):
                    after = (untrusted, first, first == known)
            request, resembled = self.links.get(index, (None, None))
            # Drift is "resembles lower-trust content more than any user request". With a
            # request that could not be embedded, that cannot be said either way: its
            # similarity is unknown, not zero.
            if resembled is not None and index not in self.incomparable:
                intent = request[0] if request else None
                link = (intent, resembled[0], units[resembled[1]].seq)
                if resembled[0] - (intent or 0.0) >= DRIFT_MARGIN:
                    alone = combine(action_indicators(unit.flags, unit.status, after))[1]
                    group = risky if alone >= RISKY else followed
                    group.setdefault(resembled[1], []).append(unit.seq)
            found[index] = action_indicators(unit.flags, unit.status, after, link)
        for action, origin in self.origins.items():
            named.setdefault(origin, []).append(units[action].seq)
        for index in content:
            scores = self.scores.get(index)
            # A fragment of a few characters resembles everything; probes need text to read.
            readable = scores is not None and len(units[index].text) >= MIN_RANKED_CHARS
            matches = self.runtime.matches(scores) if readable else ()
            found[index] = content_indicators(
                units[index].flags,
                named.get(index, ()),
                followed.get(index, ()),
                risky.get(index, ()),
                matches,
                self.spans.get(index, 0),
            )
        return found

    def _ioc_records(self):
        records = []
        for (kind, value), seen in self.iocs.items():
            uses = [
                {"seq": self.units[use["unit"]].seq, "role": use["role"]}
                | {"status": self.units[use["unit"]].status}
                for use in seen["uses"]
            ]
            indicators = ioc_indicators(kind, value, seen | {"uses": uses})
            if not indicators:
                continue
            labels, confidence, level = combine(indicators)
            record = {"type": "ioc", "kind": kind, "value": value, "labels": labels}
            record |= {"confidence": confidence, "level": level, "indicators": indicators}
            record |= {"first_seq": seen["first"], "first_seen_in": seen["by"]}
            record |= {"mentions": seen["mentions"], "count": seen["count"], "seqs": seen["seqs"]}
            record["uses"] = [use for use in uses if use["role"] != "agent"][:16]
            records.append(record)
        records.sort(key=lambda record: (-record["confidence"], record["first_seq"]))
        return records

    # --- summarize -----------------------------------------------------------------------------

    def _entry(self, index, indicators):
        unit = self.units[index]
        labels, confidence, level = combine(indicators)
        kind = "action" if unit.kind == "action" else "content"
        entry = {"type": kind, "seq": unit.seq, "ts": unit.ts, "trust": unit.trust}
        entry |= {"labels": labels, "confidence": confidence, "level": level}
        entry["indicators"] = indicators
        if unit.kind == "action":
            entry |= {"tool": unit.tool, "status": unit.status}
            entry["target"] = clip(unit.origin, TARGET_CHARS)
            entry |= effects_record(unit)
            if unit.status == "proposed" and (
                len(unit.origin) > TARGET_CHARS or unit.chars > len(unit.text)
            ):
                # A pending call is one a decision may be about: the lookup reads its whole target
                # and the files its whole words name, never the words clipped here (MR199-1).
                entry["whole"] = {"target": unit.origin, "named": unit.named}
            entry["lifecycle"] = [
                {"state": state, "seq": seq, "ts": ts} for state, seq, ts in unit.lifecycle
            ]
            if not unit.proposal_seen:  # the lifecycle then starts at what was seen instead
                entry["proposal_observed"] = False
            entry["text"] = unit.text
            if unit.chars > len(unit.text):
                entry["text_chars"] = unit.chars  # the call was longer than the text kept here
            # How many pieces the call's result came in, schema apart, for every call that
            # is kept: a reader can then tell a result shown whole from its head and tail.
            # None came back for a call that returned with nothing; where no result was
            # seen at all the count is absent, which is "not known" and never "none".
            if unit.call_id in self.returned_by:
                pieces = [index for index, schema in self.returned_by[unit.call_id] if not schema]
                entry["result_pieces"] = len(pieces)
            elif unit.status == "returned":
                entry["result_pieces"] = 0
            if self._returned.get(unit.call_id):  # which pieces its result came in
                # Identities are listed up to a cap. result_chunks is the true count, so a
                # reader can tell a short list from a whole one.
                entry["result_chunks"] = len(self._returned[unit.call_id])
                entry["results"] = self._returned[unit.call_id][:MAX_RESULT_IDS]
            if unit.call_sha256:
                entry["call_sha256"] = unit.call_sha256
            request, resembled = self.links.get(index, (None, None))
            if request is not None:
                entry["request"] = {"seq": self.units[request[1]].seq, "sim": round(request[0], 3)}
            if resembled is not None:
                near = self.units[resembled[1]]
                entry["resembles"] = {"seq": near.seq, "sim": round(resembled[0], 3)}
                entry["resembles"]["sha256"] = self._digest(near)
        else:
            scores = self.scores.get(index)
            top = np.argsort(-scores)[:2] if scores is not None else ()
            entry |= {"source": unit.source, "origin": clip(unit.origin, 240), "text": unit.text}
            entry["ref"] = {"field": unit.where, "start": unit.span[0], "end": unit.span[1]}
            if aside(unit.tool, unit.where):  # schema, not what the call said
                entry["aside"] = True
            entry["sha256"] = self._digest(unit)
            entry["scores"] = {
                self.runtime.probes[i]["id"]: round(float(scores[i]), 3) for i in top
            }
            if unit.tool:
                entry["tool"] = unit.tool
            if unit.occurrences:
                keys = ("seq", "source", "origin", "call_id", "field", "start", "end")
                entry["seen_again"] = [
                    dict(zip(keys, again[:7], strict=True)) | ({"aside": True} if again[7] else {})
                    for again in unit.occurrences
                ]
            if self.unlisted.get(index):  # sightings there was no room to list
                entry["seen_again_more"] = self.unlisted[index]
        if unit.call_id:
            entry["call_id"] = unit.call_id
        if unit.actor:
            entry["actor"] = unit.actor
        if unit.agent_id:
            entry["agent_id"] = unit.agent_id
        if unit.notes:
            entry["notes"] = unit.notes
        if unit.iocs:
            named = [f"{kind}:{value}" for kind, value in dict.fromkeys(unit.iocs)]
            entry["iocs"] = named[:ENTRY_IOCS]
        return entry

    @staticmethod
    def _digest(unit):
        return hashlib.sha256(unit.text.encode("utf-8", "surrogatepass")).hexdigest()[:16]

    def _prompt_entries(self):
        entries = []
        for prompt in self._kept_prompts():
            basis = "invisible or direction-changing characters"
            found = [{"label": "hidden_text", "confidence": PROMPT_HIDDEN, "basis": basis}]
            indicators = found if prompt["hidden"] else []
            labels, confidence, level = combine(indicators)
            entry = {"type": "prompt", "seq": prompt["seq"], "ts": prompt["ts"], "trust": "user"}
            entry |= {"labels": labels, "confidence": confidence, "level": level}
            entry |= {"indicators": indicators, "text": prompt["text"], "chars": prompt["chars"]}
            entry |= {"channel": prompt["channel"], "notes": prompt["notes"]}
            if "question" in prompt:  # asked by the agent; only the answer is the user's
                entry["question"] = prompt["question"]
            if prompt["noted"]:  # where in a long request the limiting or permitting words are
                entry["noted"] = prompt["noted"]
            if prompt["iocs"]:
                entry["iocs"] = [f"{kind}:{value}" for kind, value in prompt["iocs"]][:ENTRY_IOCS]
            entries.append(entry)
        return entries

    def _kept_prompts(self):
        """The requests a summary keeps when there are too many: the first, then those that limit,
        permit or withdraw something, then the latest, up to the configured number."""
        room = self.runtime.config.max_prompts
        if len(self.prompts) <= room:
            return self.prompts
        keep = {0}
        noted = [i for i, prompt in enumerate(self.prompts) if prompt["notes"] and i]
        keep |= set(noted[-(room - 1) :])
        for index in range(len(self.prompts) - 1, 0, -1):
            if len(keep) >= room:
                break
            keep.add(index)
        return [self.prompts[index] for index in sorted(keep)]

    def _vector(self, indexes):
        indexes = [i for i in indexes if i < self.embedded and i not in self.unembedded]
        if not indexes:
            return None
        dim = min(self.runtime.config.vector_dim, self.vectors.shape[1])
        mean = normalize(normalize(self.vectors[indexes].mean(axis=0))[:dim])
        return [round(float(value), 4) for value in mean]

    def _coverage(self):
        """What was captured and scanned, and every known reason the picture is incomplete."""
        counts = self.counts
        # Sightings of a piece beyond those listed on it. Every one was scanned and is counted
        # as its call's result: only the list a line prints is short, and this says by how much.
        counts["sightings_not_listed"] = sum(self.unlisted.values())
        gaps = []
        if not counts["session_starts"]:
            gaps.append("no session start was seen: earlier activity is unknown")
        if counts["corrupt_events"]:
            gaps.append(f"{counts['corrupt_events']} events could not be read")
        if self.unhandled:
            names = ", ".join(f"{name} x{count}" for name, count in sorted(self.unhandled.items()))
            gaps.append(f"events of kinds this version does not interpret: {names}")
        if counts["clipped_chars"]:
            gaps.append(f"{counts['clipped_chars']} characters were clipped by the hook")
        if counts["unscanned_chars"]:
            gaps.append(f"{counts['unscanned_chars']} characters were not scanned")
        if counts["partial_commands"]:
            gaps.append(f"{counts['partial_commands']} commands were not analysed in full")
        if counts["unreadable_files"]:
            gaps.append(f"{counts['unreadable_files']} instruction files could not be read")
        if counts["observables_dropped"]:
            gaps.append(f"{counts['observables_dropped']} observables were not tracked")
        if counts["unembedded"]:
            gaps.append(
                f"{counts['unembedded']} pieces of text could not be embedded: "
                "they were not compared with anything"
            )
        if counts["embedded_in_part"]:
            gaps.append(
                f"{counts['embedded_in_part']} pieces of text were too long for the model to "
                "be given whole: the rest of each was not compared with anything"
            )
        reported = ("clipped_chars", "unscanned_chars", "corrupt_events", "unreadable_files")
        reported += ("observables_dropped", "echoed_fields", "duplicate_chunks", "redactions")
        reported += ("unembedded", "sightings_not_listed", "windowed", "embedded_in_part")
        # Calls first seen at their result. Every event that was captured was still scanned, so
        # this is not a gap in scanning: it is counted here and marked on each such call.
        reported += ("proposals_not_observed",)
        return {
            "events": self.events,
            "log_bytes": self.log_bytes,
            "units": len(self.units),
            # True only when every captured event was read and scanned in full. It says nothing
            # about what the hooks never deliver; that is listed in not_captured.
            "scanned_all_captured": not gaps,
            "gaps": gaps,
            "not_captured": NOT_CAPTURED.get(self.agent, []),
            "saw_session_start": bool(counts["session_starts"]),
            "unhandled_events": dict(self.unhandled),
            **{key: counts[key] for key in reported},
        }

    def summary(self):
        """The summary as records, one per line of summary.jsonl (call flush() first).

        A session record comes first, then the entries in session order (user requests, agent
        actions, lower-trust content, the last agent message), then the observables, the session
        vectors and a text digest. Entries and observables each carry labels, a confidence, a
        level and the indicators behind them.
        """
        config, units = self.runtime.config, self.units
        prompts = [index for index, unit in enumerate(units) if unit.kind == "prompt"]
        actions = [index for index, unit in enumerate(units) if unit.kind == "action"]
        content = [index for index, unit in enumerate(units) if unit.trust in LOWER_TRUST]
        flow = self._flow(actions, content)
        indicators = self._indicators(actions, content, flow)
        scored = {index: combine(found) for index, found in indicators.items()}

        marked = [index for index, (_, _, level) in scored.items() if level != "none"]
        marked.sort(key=lambda index: (-scored[index][1], -index))
        kept = set(marked[: config.max_marked])
        calm = [index for index in actions if index not in kept]
        recent = calm[-8:]
        room = config.max_actions - len(recent)
        # An older call with no vector is kept by recency; those with one, by how unlike
        # what is already kept they are.
        older = self._unseen(calm[:-8], room)
        anchors = sorted(kept & set(actions)) + recent
        older += self._diverse(calm[:-8], {}, room - len(older), anchors=anchors)
        # Content that a kept or recent action took something from, or most resembles, is part of
        # that action's story whether or not the content is marked.
        cited = {origin for action, origin in self.origins.items() if action in kept}
        for action in set(actions[-8:]) | (kept & set(actions)):
            resembled = self.links.get(action, (None, None))[1]
            if resembled is not None:
                cited.add(resembled[1])
        # What a recent or marked call returned is part of its outcome however short or plain it
        # is ("41 passed"): keep the head and the tail of each result.
        self._returned = {}  # call id -> how many pieces its result came in
        # Every piece each call returned, with whether it was schema there (member names,
        # the flags of the agents' own tools): what a piece was in one result says nothing
        # of another. Kept as the results arrived, so a result that repeats an earlier one
        # for the hundredth time is still that call's result.
        results = self.returned_by
        # A request that sets a condition ("only if the tests pass") may be settled by any later
        # result, so the results of the calls after it are kept as well, newest first.
        waits = [prompt["seq"] for prompt in self.prompts if "condition" in prompt["notes"]]
        since = [i for i in actions if waits and units[i].seq > waits[0]][-64:]
        for action in set(actions[-8:]) | (kept & set(actions)) | set(since):
            returned = results.get(units[action].call_id, ())
            # Schema is scanned like everything else but is not what a call is said to have
            # returned.
            said = [index for index, schema in returned if not schema]
            self._returned[units[action].call_id] = [self._digest(units[i]) for i in said]
            # Its numbers and flags are kept whole (an exit status is the outcome), and of the
            # rest the head and the tail.
            scalars = [i for i in said if units[i].where.endswith(("{values}", "{items}"))]
            rest = [i for i in said if i not in scalars]
            cited |= set(scalars) | ({rest[0], rest[-1]} if rest else set())
        cited -= kept
        rank = {
            index: float(self.scores[index].max())
            for index in content
            if index in self.scores and len(units[index].text) >= MIN_RANKED_CHARS
        }
        rest = [index for index in rank if index not in kept and index not in cited]
        # Content with no vector has no probe score to be ranked by. It stands with the
        # ranked pieces as a candidate and is kept by recency.
        dark = [
            index
            for index in content
            if index in self.unembedded and index not in kept and index not in cited
            if len(units[index].text) >= MIN_RANKED_CHARS
        ]
        anchors = sorted((kept | cited) & set(content))
        ranked = self._unseen(rest + dark, config.max_content)
        ranked += self._diverse(rest, rank, config.max_content - len(ranked), anchors=anchors)
        chosen = sorted(kept | set(recent) | set(older) | cited | set(ranked))

        entries = self._prompt_entries() + [self._entry(i, indicators[i]) for i in chosen]
        # Every message that carries an indicator or names an observable, and the latest one.
        # Marked ones first, then the most recent that only name something, within the cap.
        earlier = self.messages[:-1]
        marked_said = [m for m in earlier if m["indicators"]][-MAX_MESSAGES:]
        room = MAX_MESSAGES - len(marked_said)
        named_said = [m for m in earlier if not m["indicators"] and m.get("iocs")]
        named_said = named_said[-room:] if room else []
        said = sorted(marked_said + named_said, key=lambda m: m["seq"]) + self.messages[-1:]
        kept_said = {m["seq"] for m in said}
        message_marks = {m["seq"]: combine(m["indicators"]) for m in self.messages}
        for message in said:
            labels, confidence, level = message_marks[message["seq"]]
            mark = {"trust": "agent", "labels": labels, "confidence": confidence, "level": level}
            entries.append({"type": "message", **message, **mark})
        lost_key = sum(
            1
            for message in self.messages
            if message["seq"] not in kept_said
            and message_marks[message["seq"]][2] in ("high", "medium")
        )
        order = {"prompt": 0, "action": 1, "content": 2, "message": 3}
        entries.sort(key=lambda entry: (entry["seq"], order[entry["type"]]))

        tally, levels = {}, Counter()
        for index, (labels, confidence, level) in scored.items():
            levels[level] += 1
            for label in labels:
                seen = tally.setdefault(label, {"count": 0, "max": 0.0, "seqs": []})
                seen["count"] += 1
                seen["max"] = max(seen["max"], confidence)
                if len(seen["seqs"]) < 8 and units[index].seq not in seen["seqs"]:
                    seen["seqs"].append(units[index].seq)
        # What the agent said counts in the session's totals like anything else that is marked.
        for message in self.messages:
            labels, confidence, level = message_marks[message["seq"]]
            if level != "none":
                levels[level] += 1
            for label in labels:
                seen = tally.setdefault(label, {"count": 0, "max": 0.0, "seqs": []})
                seen["count"] += 1
                seen["max"] = max(seen["max"], confidence)
                if len(seen["seqs"]) < 8 and message["seq"] not in seen["seqs"]:
                    seen["seqs"].append(message["seq"])
        iocs = self._ioc_records()
        shown = set(chosen)
        sources = sorted(self.sources.values(), key=lambda entry: entry["last"])
        missed = Counter(units[i].tool for i in actions if i not in shown)
        # Each call the summary does not keep is catalogued with what became of it, the event that
        # brought that back, and the effects recorded from its whole words (MR199-1).
        dropped = [
            {"seq": units[i].seq, "ts": units[i].ts, "tool": units[i].tool}
            | {"status": units[i].status, "target": clip(units[i].origin, TARGET_CHARS)}
            | {"call_id": units[i].call_id, "result": result_seq(units[i])}
            | effects_record(units[i])
            for i in actions
            if i not in shown
        ]
        catalogue = dropped[-config.max_dropped_calls :] if config.max_dropped_calls else []
        session = {
            "type": "session",
            "schema": SCHEMA,
            "agent": self.agent,
            "session_id": self.session_id,
            "updated_at": round(time.time(), 3),
            **self.info,
            "coverage": self._coverage(),
            # What embedded and what marked this summary. It is about the summary and is no
            # part of the text a detector reads.
            **self.runtime.identity(),
            # Confidence ranks; it is a combination of hand-set priors, not a measured probability.
            "confidence": "uncalibrated ranking score",
            "flow": flow,
            "marked": {name: levels[name] for name in ("high", "medium", "low")},
            "iocs": dict(Counter(record["level"] for record in iocs)),
            "labels": dict(sorted(tally.items(), key=lambda item: -item[1]["max"])),
            "sources": [
                {key: entry[key] for key in ("seq", "source", "origin", "chunks")}
                for entry in sources[-16:]
            ],
            "context": self.context[-32:],
            "omitted": {
                "prompts": max(0, len(self.prompts) - config.max_prompts),
                "noted_prompts": sum(bool(p["notes"]) for p in self.prompts)
                - sum(bool(p["notes"]) for p in self._kept_prompts()),
                "marked": max(0, len(marked) - config.max_marked),
                "iocs": max(0, len(iocs) - config.max_iocs),
                "actions": dict(missed.most_common()),
                "content": sum(1 for index in content if index not in shown),
                "sources": max(0, len(sources) - 16),
                "context": max(0, len(self.context) - 32),
                "messages": len(self.messages) - len(said),
                "key_messages": lost_key,  # medium or high ones among them
            },
            "recent_calls": list(self.recent_calls),
            "dropped_calls": catalogue,
            "dropped_calls_not_listed": len(dropped) - len(catalogue),
        }
        iocs = iocs[: config.max_iocs]
        vectors = {
            "type": "vectors",
            "dim": min(config.vector_dim, self.vectors.shape[1]),
            "intent": self._vector(prompts),
            "actions": self._vector(actions),
            "content": self._vector(content),
        }
        view = {"session": session, "entries": entries, "iocs": iocs}
        digest = {"type": "digest", "max_chars": config.max_chars}
        digest["text"] = render(view, config.max_chars)
        return [session, *entries, *iocs, vectors, digest]
