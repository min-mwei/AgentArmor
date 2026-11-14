"""Read Claude Code and Codex hook payloads as typed units. Standard library only.

A unit is one piece of session evidence with its provenance fixed by the hook that delivered it:
who authored the text (the user, the agent, or a lower-trust source the agent read) and where it
came from. Provenance is never inferred from the text itself.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

from .facts import PERSISTENT, expand, host_flags, hosts, path_classes, shell_facts
from .store import EVENTS, read_events, session_dir

ACTION_CHARS = 1200  # an action's own text, as embedded and shown; the whole call is still scanned
TARGET_CHARS = 240  # an action's target (its command, path or server/tool) as a summary shows it
ENTRY_IOCS = 8  # observables listed on one entry of a summary; its text may name more
CHUNK_CHARS = 320  # lower-trust content is embedded and quoted whole in pieces of this size
MAX_CHUNKS = 1024  # per event: every character the hook keeps; anything beyond is reported

_SHELL = {"Bash", "PowerShell", "Monitor", "shell", "exec_command", "local_shell"}
_READS = {"Read", "NotebookRead", "Glob", "Grep", "LS", "read_file", "list_dir", "view_image"}
_WRITES = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}
_SUBAGENT = {"Agent", "Task", "spawn_agent", "Workflow"}
# The agents' own tools, whose result schema is theirs. Anything else is someone else's output.
BUILTIN = _SHELL | _READS | _WRITES | _SUBAGENT
BUILTIN |= {"WebFetch", "WebSearch", "AskUserQuestion", "ExitPlanMode", "TodoWrite", "Skill"}
BUILTIN |= {"ToolSearch", "KillShell", "BashOutput", "SlashCommand", "update_plan", "write_stdin"}
_PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$|^\*\*\* (Move) to: (.+)$", re.M)
_PATCH_FRAME = ("*** Begin Patch", "*** End Patch", "*** End of File")  # headers naming no file
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
SCALARS, KEYS = "{values}", "{keys}"  # stand-ins for a key: scalar members, member names
_MCP_WRITE = re.compile(
    r"create|update|send|post|write|comment|push|upload|publish|delete|edit|add|reply|merge|share",
    re.I,
)
# Result fields of the agents' own tools that hold an identifier, never prose. A value under one
# of these keys is left unscanned only if it also looks like an identifier, and is counted.
_BOOKKEEPING = {
    "type",
    "id",
    "tool_use_id",
    "agentId",
    "resolvedModel",
    "status",
    "mode",
    "filePath",
}


@dataclass(slots=True)
class Unit:
    seq: int  # 1-based position of the hook event in the session log
    ts: float
    kind: str  # prompt | action | output
    trust: str  # user | agent | untrusted | derived
    source: str  # user, agent, or where lower-trust text came from: file, web, mcp, shell, ...
    origin: str  # the path, host, server/tool or command the text came from
    text: str
    tool: str = ""
    call_id: str = ""
    actor: str = ""  # subagent type when the event fired inside one
    agent_id: str = ""  # which subagent, when the hook says
    status: str = ""  # actions: proposed | returned | failed | denied
    lifecycle: list = field(default_factory=list)  # actions: (state, seq, ts) as events arrive
    proposal_seen: bool = True  # actions: False when a later event first revealed the call
    flags: dict = field(default_factory=dict)  # flag -> the path, host or text that raised it
    notes: list = field(default_factory=list)  # facts that explain an entry without marking it
    hosts: set = field(default_factory=set)
    iocs: list = field(default_factory=list)  # (kind, value) observables named in the text
    where: str = ""  # the event field the text was cut from, such as tool_response.stdout
    span: tuple = (0, 0)  # character offsets of the text within that field
    occurrences: list = field(
        default_factory=list
    )  # content seen again: (seq, source, origin, call)
    chars: int = 0  # actions: length of the whole call text, when the kept text is shorter
    call_sha256: str = ""  # actions: the hook's hash of the call's input, to match it elsewhere
    # actions: the files the call's whole words show it observing, changing, naming with an effect
    # not known, or looking at (history.call_effects), read when it is recorded, before anything
    # is clipped for display; and whether the hook clipped its input, so they may not be whole.
    effects: dict = field(default_factory=dict)
    input_clipped: bool = False
    patch_unread: bool = (
        False  # actions: a patch header was not read, so its writes are not all known
    )
    named: list = field(default_factory=list)  # actions: the files its whole words name


def clip(text, limit):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _string(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return " ".join(value)
    return ""


def chunk_spans(text, size=CHUNK_CHARS, limit=MAX_CHUNKS):
    """Cut text into consecutive spans of at most size characters, at line or sentence ends.

    Returns (spans, unscanned): unscanned counts the characters left over once limit is reached.
    """
    spans, start, end_of_text = [], 0, len(text)
    while start < end_of_text and len(spans) < limit:
        while start < end_of_text and text[start].isspace():
            start += 1
        if start >= end_of_text:
            break
        end = min(start + size, end_of_text)
        if end < end_of_text:
            window = text[start:end]
            for mark in ("\n", ". ", " "):
                cut = window.rfind(mark)
                if cut >= size // 2:
                    end = start + cut + len(mark)
                    break
        spans.append((start, end))
        start = end
    return spans, len(text[start:].strip())


def _patch_files(patch):
    files = []
    for match in _PATCH_FILE.finditer(patch):
        verb, path = (match[1], match[2]) if match[1] else (match[3], match[4])
        files.append((verb.lower(), path.strip()))
    return files


_AGENT_HOME = re.compile(r"(/.+?)/\.(?:claude|codex)/")  # the agents keep transcripts under ~


def agent_home(transcript_path):
    """The home directory a transcript path lies under, or None when it does not say.

    Both agents keep a session's transcript under ~/.claude or ~/.codex, so the path their
    events carry names the directory "~" stands for in that session. Until an event says so,
    a summary reads "~" as the home of whoever runs the summarizer, which is right for a
    daemon beside the agent and wrong for a session replayed somewhere else.
    """
    match = _AGENT_HOME.match(transcript_path) if isinstance(transcript_path, str) else None
    return match[1] if match else None


def describe_call(tool, tool_input, cwd="", home="", real=None, root=None):
    """What one tool call is: its text, target, flags, hosts, classified paths and full body.

    cwd resolves relative paths; root is the workspace a write is expected to stay inside (cwd if
    not given). real maps hook payload fields ("tool_input.file_path") to symlink-resolved paths.
    Each flag maps to the path, host or command text that raised it. text is cut for display;
    body is everything the agent wrote in the call, for scanning.
    """
    args = tool_input if isinstance(tool_input, dict) else {}
    real = real or {}
    root = cwd if root is None else root
    flags, call_hosts, paths, prose = {}, set(), {}, False
    command = _string(args.get("command"))

    def classify(path, field_name, write):
        lexical = expand(path, cwd, home)
        classes = path_classes(lexical, root, home)
        resolved = real.get(f"tool_input.{field_name}")
        if resolved:
            flags.setdefault("symlink", f"{lexical} -> {resolved}")
            classes |= path_classes(resolved, root, home)
            paths[resolved] = classes
        paths[lexical] = classes
        shown = resolved or lexical
        if "sensitive" in classes:
            flags.setdefault("sensitive", shown)
        if write and (persistent := sorted(classes & PERSISTENT)):
            flags.setdefault("persistence_write", f"{shown} ({','.join(persistent)})")
        if write and "observer" in classes:
            flags.setdefault("observer_tamper", shown)
        if write and "lockfile" in classes:
            flags.setdefault("lockfile_edit", shown)
        if "outside" in classes:
            flags.setdefault("outside_write" if write else "outside_read", shown)
        return lexical

    writes, writes_whole = [], True  # every file a file tool writes, and whether all were read
    if tool == "apply_patch":
        files = _patch_files(command)
        # Every file the patch adds, updates, deletes or moves to, not only the first (MR200-1).
        writes = [classify(path, "command", write=True) for _, path in files]
        # A header this reading does not know, or a patch with no file it can read, leaves what
        # it writes not all known.
        unknown = [
            line
            for line in command.splitlines()
            if line.startswith("*** ") and line.strip() not in _PATCH_FRAME
            if not _PATCH_FILE.match(line)
        ]
        writes_whole = not unknown and (bool(files) or not command.strip())
        added = " ".join(line[1:].strip() for line in command.splitlines() if line[:1] == "+")
        listing = ", ".join(f"{verb} {path}" for verb, path in files) or "(no files parsed)"
        text, target, body = (
            f"apply_patch {listing}: {added}",
            files[0][1] if files else "",
            command,
        )
        prose = True
    elif tool in _SHELL or (command and tool not in _WRITES):
        found = shell_facts(command, cwd, home)
        flags.update(found["flags"])
        call_hosts |= found["hosts"]
        paths.update(found["paths"])
        text, target, body = f"{tool}: {command}", command, command
    elif tool in _WRITES:
        field_name = "notebook_path" if "notebook_path" in args else "file_path"
        target = classify(_string(args.get(field_name)), field_name, write=True)
        writes = [target] if target else []
        edits = args.get("edits") if isinstance(args.get("edits"), list) else [args]
        body = "\n".join(
            _string(edit.get(key))
            for edit in edits
            if isinstance(edit, dict)
            for key in ("content", "new_string", "new_source")
            if edit.get(key)
        )
        text, prose = f"{tool} {target}: {body}", True
    elif tool in _READS:
        names = ("file_path", "notebook_path", "path")
        field_name = next((key for key in names if args.get(key)), "")
        target = classify(_string(args[field_name]), field_name, write=False) if field_name else ""
        pattern = _string(args.get("pattern"))
        text = f"{tool} {json.dumps(pattern)} in {target or cwd}" if pattern else f"{tool} {target}"
        body = pattern
    elif tool == "WebFetch":
        target = _string(args.get("url"))
        call_hosts |= hosts(target)
        flags["network"] = ", ".join(sorted(call_hosts)) or target
        flags.update(host_flags(call_hosts))
        body = f"{target} {_string(args.get('prompt'))}"
        text = f"WebFetch {body}"
    elif tool == "WebSearch":
        target = body = _string(args.get("query"))
        flags["network"] = "web search"
        text = f"WebSearch {json.dumps(target)}"
    elif tool in _SUBAGENT:
        target = _string(args.get("subagent_type") or args.get("agent_type") or args.get("name"))
        flags["subagent"] = target
        body = _string(args.get("prompt") or args.get("message") or args.get("description"))
        text, prose = f"{tool}({target}): {body}", True
    else:
        target, body, prose = "", _json(args), True
        if tool.startswith("mcp__"):
            target = "/".join(tool.split("__")[1:3])
            flags["mcp"] = target
            if _MCP_WRITE.search(tool.split("__")[-1]):
                flags["mcp_write"] = target  # by its name, the tool changes or sends something
        text = f"{tool} {body}"
        call_hosts |= hosts(body)
    # Whatever else the agent wrote in the call (a command's description, the text an edit
    # replaces) is part of what it proposed. It joins the call's text under its field name, so it
    # is scanned, embedded and shown as the agent's own words.
    covered = f"{text}\n{target}\n{body}"
    extras = []
    for path, key, value in fields(args, "tool_input"):
        name = path.removeprefix("tool_input").lstrip(".")
        if key == SCALARS:
            # Numbers and flags change what a call does (run in background, replace every
            # match): each stays beside its own name.
            name = name.removesuffix("{values}").removesuffix("{items}")
            extras.append(f"{name}: {value}" if name else value)
        elif key != KEYS and value.strip() and value not in covered:
            extras.append(f"{name}: {value}")
    extras += [f"{name}: {shown}" for name, shown in _empty(args)]
    extra = "\n".join(extras)
    if extra:
        text = f"{text.strip()}\n[{extra}]"
    return {
        "extra": extra,  # input the tool-specific reading above did not cover
        "text": clip(text.strip(), ACTION_CHARS),
        "chars": len(text.strip()),  # of the whole call text, which the kept text may cut short
        "whole": text.strip(),  # the whole call text, for what is read from it before clipping
        "writes": writes,  # every file a file tool writes: each file of a patch, in its order
        "writes_whole": writes_whole,  # False when a patch holds a header that was not read
        "target": target,
        "flags": flags,
        "hosts": call_hosts,
        "paths": paths,
        "body": body,
        "prose": prose,  # body is text the agent composed, not a command or a search pattern
    }


def call_words(tool, text):
    """A call's own words: the text describe_call() gave it, without the tool's name.

    describe_call() writes a call as its tool's name, one separator and the call: "Bash: ls",
    "Read /srv/notes.txt", "Task(reviewer): check the diff". Exactly that name and that
    separator come off again. Whatever follows is the call's, a colon or a space at its start
    included: ": > notes.txt" is a command that empties a file, and "> notes.txt" is not what
    the agent wrote.
    """
    if not text.startswith(tool):
        return text
    rest = text[len(tool) :]
    if rest.startswith(": "):
        return rest[2:]
    if rest == ":" or rest.startswith(":\n"):  # a shell call with no command: its space was
        return rest[1:]  # stripped with the end of the line
    return rest[1:] if rest.startswith(" ") else rest


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def fields(value, path, key=""):
    """Everything readable under a JSON value as (field path, key, text).

    A list of strings is one text. Numbers, booleans and nulls are text too, wherever they sit:
    an object's are one line at <path>{values} ("passed: true, checks: 41", a name that is not a
    plain word quoted: "\"a, b\": 1"), a list's one line at
    <path>{items}, and a bare one is itself. A result's verdict is often not a string. The member
    names of an object are text the agent read as well, one line at <path>{keys}.
    """
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        value = "\n".join(value)
    if isinstance(value, str):
        yield path, key, value
    elif _scalar(value):
        yield path, SCALARS, json.dumps(value)
    elif isinstance(value, dict):
        plain = [scalar_entry(name, item) for name, item in value.items() if _scalar(item)]
        if plain:
            yield path + "{values}", SCALARS, ", ".join(plain)
        if value:
            yield path + "{keys}", KEYS, ", ".join(str(name) for name in value)
        for name, item in value.items():
            if _scalar(item):
                continue  # already in {values}
            name = str(name)
            # A key that is not a plain word is quoted, so "a.b" and a nested a -> b differ.
            step = f".{name}" if _KEY.fullmatch(name) else f"[{json.dumps(name)}]"
            yield from fields(item, path + step, name)
    elif isinstance(value, list):
        plain = [json.dumps(item) for item in value if _scalar(item)]
        if plain:
            yield path + "{items}", SCALARS, ", ".join(plain)
        for index, item in enumerate(value):
            if not _scalar(item):
                yield from fields(item, f"{path}[{index}]", key)


def _empty(value, path=""):
    """Members that hold nothing ("", {}, []), as (field path, literal): given, though empty."""
    if isinstance(value, dict):
        for name, item in value.items():
            name = str(name)
            step = name if _KEY.fullmatch(name) else f"[{json.dumps(name)}]"
            here = f"{path}.{step}".lstrip(".") if step == name else f"{path}{step}"
            if item in ("", {}, []) or (isinstance(item, str) and not item.strip()):
                yield here, json.dumps(item)
            else:
                yield from _empty(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict | list):
                yield from _empty(item, f"{path}[{index}]")


def scalar_entry(name, item):
    """One member of an object's line of values: name: value, the name quoted unless plain.

    A name that is not a plain word is written as a JSON string, so a comma, colon or quote
    inside a name cannot be read as the boundary between two members.
    """
    name = str(name)
    # Not escaped to ASCII: the name is text the agent read, and is scanned as it was written.
    shown = name if _KEY.fullmatch(name) else json.dumps(name, ensure_ascii=False)
    return f"{shown}: {json.dumps(item)}"


_ENTRY = re.compile(
    r"""([A-Za-z_][A-Za-z0-9_-]*|"(?:[^"\\]|\\.)*"): """
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|NaN|-?Infinity)(?:, |$)"
)


def scalar_entries(text):
    """The members a line of values holds, as [(name, value, start, end)], or None.

    None unless the whole text is such a line. Any string may be asked: text that only looks
    like one (a bad escape in a quoted name, a number JSON does not allow) is not one.
    """
    found, at = [], 0
    while at < len(text):
        entry = _ENTRY.match(text, at)
        if not entry:
            return None
        try:
            name = json.loads(entry[1]) if entry[1].startswith('"') else entry[1]
            value = json.loads(entry[2])
        except ValueError:
            return None
        if not isinstance(name, str):
            return None
        found.append((name, value, entry.start(), entry.end(2)))
        at = entry.end()
    return found if found and not text.endswith(", ") else None


def field_spans(field, text, size=CHUNK_CHARS, limit=MAX_CHUNKS):
    """chunk_spans for the text of one field: a line of values is cut between members only.

    A member ("remaining: 7") is never split across two pieces, so each piece is itself whole
    members. Other text, and a line with a member longer than a piece, is cut as usual.
    """
    members = scalar_entries(text) if field.endswith(SCALARS) else None
    if not members or any(end - start > size for _, _, start, end in members):
        return chunk_spans(text, size, limit)
    spans, start, last = [], members[0][2], members[0][3]
    for _, _, begin, end in members[1:]:
        if end - start > size:
            spans.append((start, last))
            start = begin
        last = end
    spans.append((start, last))
    kept = spans[:limit]
    return kept, sum(end - begin for begin, end in spans[limit:])


def _scalar(value):
    return value is None or isinstance(value, bool | int | float)


def output_blocks(tool, tool_input, response, root="tool_response", scanned=None):
    """Every text a tool result carries, as (event field, text), and how many were echoes.

    Nothing is left unscanned because of its name or the tool it came from: member names,
    identifiers, numbers and flags are all text. The one exception is a string that repeats,
    character for character, a string of the call's input that the call's own scan covered.
    scanned is that covered text: the call's target, its body and its other input fields.
    Echoes are counted.
    """
    if scanned is None:
        scanned = describe_call(tool, tool_input)
        scanned = f"{scanned['target']}\n{scanned['body']}\n{scanned['extra']}"
    echoed = {text for text in _strings(tool_input) if len(text) >= 8 and text in scanned}
    blocks, echoes = [], 0
    for path, key, text in fields(response, root):
        if not text.strip():
            continue
        if key not in (SCALARS, KEYS) and text in echoed:
            echoes += 1
        else:
            blocks.append((path, text))
    return blocks, echoes


def aside(tool, field):
    """Whether a result field is schema rather than what the call said: member names anywhere,
    and for the agents' own tools their bookkeeping identifiers ("type": "text"). Asides are
    scanned and tagged like any text; they are just not what is quoted as a call's result.
    Numbers and flags are never asides: "interrupted: true" or "exit_code: 1" is the outcome."""
    if field.endswith("{keys}"):
        return True
    last = re.split(r"[.\[]", field)[-1]
    return tool in BUILTIN and last in _BOOKKEEPING


def field_text(record, field):
    """The text at a field path in one record of the event log, or None if it is not there."""
    if field.startswith("file:"):  # an instruction file the hook read for the event
        files = (record.get("meta") or {}).get("files") or ()
        return next((item.get("content") for item in files if item.get("path") == field[5:]), None)
    event = record.get("event") if isinstance(record.get("event"), dict) else {}
    root = re.match(r"\w+", field)
    if root is None or root[0] not in event:
        return None
    found = fields(event.get(root[0]), root[0])
    return next((text for path, _, text in found if path == field), None)


def evidence(agent, session_id, entry):
    """The whole text a content entry was cut from, read back from the session's event log.

    Returns (text, start, end) with the entry's own text at text[start:end]. Returns None when
    the entry carries no reference, the log no longer holds it, or what the reference finds is not
    the text the entry was cut from (its hash is checked), so a reference never supplies different
    evidence. The log is already redacted: reading around an excerpt cannot bring a secret back.
    """
    ref = entry.get("ref")
    if not ref:
        return None
    try:
        records, _ = read_events(session_dir(agent, session_id) / EVENTS, limit=entry["seq"])
    except OSError:
        return None
    text = field_text(records[-1], ref["field"]) if len(records) == entry["seq"] else None
    if text is None:
        return None
    excerpt = text[ref["start"] : ref["end"]].strip().encode("utf-8", "surrogatepass")
    if hashlib.sha256(excerpt).hexdigest()[:16] != entry.get("sha256"):
        return None
    return text, ref["start"], ref["end"]


def output_source(tool, tool_input, call):
    """(trust, source, origin) of a tool result: everything a tool returns is lower-trust data."""
    args = tool_input if isinstance(tool_input, dict) else {}
    if tool in _SUBAGENT:
        return "derived", "subagent", call["target"]
    if tool in ("WebFetch", "WebSearch"):
        return "untrusted", "web", next(iter(sorted(call["hosts"])), "search")
    if tool.startswith("mcp__"):
        return "untrusted", "mcp", call["target"]
    if tool in _READS:
        return "untrusted", "file", call["target"] or _string(args.get("pattern"))
    if tool in _SHELL or args.get("command"):
        return "untrusted", "shell", clip(_string(args.get("command")), 120)
    return "untrusted", "tool", tool
