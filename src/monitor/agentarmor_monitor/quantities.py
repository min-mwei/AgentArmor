"""Cumulative-quantity observations (agentarmor-quantities/v2, part B of observe.py). Stdlib only.

Off unless the operator gives `serve --session-observations on --observation-quantities on`; with
it off, the observation section is byte for byte part A's. Research and shadow; policy_ready stays
false.

What it reads (MR296 section 3 B, bounded). Only explicit caps the user stated with a number and a
unit, in a user-channel prompt before the call ("no more than five case records", "at most 3
files", "cap delivered bundles at one", "limit uploads to 2"), and only quantities a recorded
result states before the call ("four sanitized records accepted", "Two snapshots were removed").
Nothing else is a cap: a number in a tool result, a file or the agent's own text is never one, and
a cap phrase with no unit word, or a unit nobody reports, stays a cap with nothing counted.

What it says, for each cap:

- the cap as said: number, unit words, the operation its sentence names (send or delete, from a
  small fixed word list; "not stated" otherwise), the scope words as said ("across this incident",
  never resolved to a window), its source (session, event, offsets) and every later user statement
  that states another cap with a shared unit word or names a unit word with revocation cue words;
- each place a result puts a number of the unit beside a verb of the operation: the number, the
  words between, the verb, the event that carries it, and what that event is: the result of a call
  (with the call's recorded status) or text the agent read from a file. A unit counts as matching
  only by a shared word (singular), and the matching word is shown; the identity of the units is
  not established. Only two forms are reports (v2, MR303): "NUMBER [of M] UNIT [were|was|have
  been|has been|had been] PAST-VERB" and "PAST-VERB NUMBER UNIT", in a clause with no negation,
  failure, modal, condition, plan or limit word and no question mark; NUMBER is a number word or
  one whole run of at most six digits (a decimal, digit group, range, fraction, percentage, sign or
  unit glued to the digits is kept whole as said and unresolved), and typographic apostrophes and
  quotes read as their ASCII forms ("weren’t" is a negation). Every other form is kept with
  its sentence as evidence, marked unresolved with the reason, and enters no arithmetic;
- each earlier call of the same operation (by its words) and what is known of it: returned with a
  stated quantity (linked to that report), returned with no quantity stated, result clipped, or
  attempted with no result returned. An attempt is never counted as an outcome, and a returned
  result is a report, not an observed effect;
- the total before the call as bounds, from the reports alone, under the assumption stated beside
  them (results report truthfully and completely; calls naming disjoint path operands are separate):
  at least the largest single report, or the sum over groups of calls with disjoint path operands
  if larger. A call naming no path operand, a second report in one result and a report not tied to
  a call may each repeat another, so each enters the lower bound only alone, and the overlap is
  said. At most: every report summed; "unknown" with the reasons whenever a candidate is
  unresolved, a call of the operation returned no stated quantity, was clipped, or has no result
  yet. Missing history
  is never zero: sessions the server did not record are outside every bound, and the line says so;
- what the proposed call's own arguments name: the values of an option named like the unit, or the
  path operands of its command; "not stated" otherwise; and the arithmetic sum of the lower bound
  and that count beside the stated number. No word here compares them: never "exceeds", never a
  verdict.

Contributions of another session with another recorded working directory are listed and not
counted; those of a session with the same directory are counted with namespace unknown.
"""

import re

from . import observe as _o

VERSION = "agentarmor-quantities/v2"
MAX_CAPS = 3
MAX_LISTED = 4

_NUMBERS = {
    w: n
    for n, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
_N = r"(\d{1,6}|" + "|".join(_NUMBERS) + r")"
_STOP = frozenset(
    "may might can could will would should must shall to per in at on during across for from of "
    "each be is are was were been that which who than and or but the a an this these those it "
    "into onto outside inside within throughout by with without".split()
)
_OPS = {
    "send": "send sent sending sends deliver delivered delivering delivers upload uploaded "
    "uploading uploads transfer transferred transferring transfers submit submitted submitting "
    "post posted posting publish published publishing export exported exporting share shared "
    "email emailed accepted",
    "delete": "remove removed removing removes delete deleted deleting deletes prune pruned "
    "pruning purge purged purging erase erased erasing wipe wiped destroy destroyed rm",
}
_OPS = {word: op for op, words in _OPS.items() for word in words.split()}
_CAPS = (
    re.compile(
        rf"\b(?:no more than|not more than|at most|a maximum of|maximum of|up to)\s+{_N}\s+"
        r"((?:[A-Za-z][\w-]*\s+){0,5}[A-Za-z][\w-]*)",
        re.I,
    ),
    re.compile(
        rf"\b(?:cap|caps|capped)\s+((?:[A-Za-z][\w-]*\s+){{0,6}}?[A-Za-z][\w-]*)\s+at\s+{_N}\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:limit|limits)\s+((?:[A-Za-z][\w-]*\s+){{0,6}}?[A-Za-z][\w-]*)\s+to\s+{_N}\b", re.I
    ),
)
_SCOPE = re.compile(
    r"\b(?:across|during|within|throughout)\s+(?:this|the|that|each|our)\s+"
    r"[A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*){0,3}",
    re.I,
)
# A clause runs to the next stop; a point before a digit ("1.5", ".5") is part of a number.
_PIECE = re.compile(r"(?:[^.;!?\n]|\.(?=\d))+")
# Typographic apostrophes and quotes read as their ASCII forms, one character for one, so that
# "weren’t" is a negation as "weren't" is and every offset still points at the original text.
_PLAIN = str.maketrans("‘’ʼ‛′“”‟″", "'" * 5 + '"' * 4)
# A token is a word or a whole numeric expression as written: a sign, digits joined by points,
# commas, slashes, colons or dashes, and a percent sign or letters glued after. Only a bare run of
# at most six digits is a number; any other expression is kept whole and left unresolved.
_TOKEN = re.compile(
    r"(?P<num>(?<![\w.,])[-+−]?\.?\d+(?:[.,/:–—-]\d+)*(?:%|[A-Za-z_]\w*)?)"
    r"|[A-Za-z][\w'-]*"
)
# The verbs a report may use: past forms only ("sent", never "send", "sends" or "sending").
_PAST = frozenset(
    "sent delivered uploaded transferred submitted posted published exported shared emailed "
    "accepted removed deleted pruned purged erased wiped destroyed".split()
)
_BE = frozenset("were was are is been have has had be".split())
_AUX = (("have", "been"), ("has", "been"), ("had", "been"), ("were",), ("was",))
_FILLERS = frozenset("up to only exactly about over all the".split())
# Any of these anywhere in the clause leaves a candidate unresolved.
_CUES = {
    word: kind
    for kind, words in {
        "negation": "not no none never nothing nobody neither nor without cannot",
        "failure": "fail failed fails failing failure unable error errors rejected refused "
        "skipped aborted",
        "modal": "will would can could may might must shall should",
        "condition": "if unless whether when once until",
        "plan": "plan plans planned planning going intend intends intended pending queued "
        "scheduled ready expect expects expected attempt attempted attempting try tried "
        "trying preview simulated simulate dry-run would-be",
        "hedge": "almost nearly partially reportedly supposedly allegedly",
    }.items()
    for word in words.split()
}
_INTERPRETERS = frozenset(
    {"python", "python3", "node", "ruby", "perl", "bash", "sh", "zsh", "deno"}
)
_SEND_PROGRAMS = frozenset({"scp", "rsync", "sftp", "ftp"})
_DELETE_PROGRAMS = frozenset({"rm", "rmdir", "unlink", "shred"})
_READERS = frozenset({"cat", "head", "tail", "less", "more", "sed", "awk", "grep", "jq"})
_SHELLS = frozenset({"Bash", "exec_command", "shell", "local_shell"})
_FILE_TOOLS = frozenset(
    {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "LS", "apply_patch"}
)
_OPERATORS = frozenset({"&&", "||", ";", "|", "&", "(", ")"})


def _number(word):
    word = word.lower()
    return int(word) if word.isdigit() else _NUMBERS.get(word)


def singular(word):
    word = word.lower().strip("-")
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "ches", "shes", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _unit_words(phrase, leading=False):
    """The unit's words: the phrase up to its first stop word or verb of an operation (the
    words that follow a number), or the whole phrase less those (a phrase before "at N")."""
    words = re.findall(r"[A-Za-z][\w-]*", phrase)
    if leading:
        return [w for w in words if w.lower() not in _STOP]
    out = []
    for word in words:
        if word.lower() in _STOP or word.lower() in _OPS:
            break
        out.append(word)
    return out


def _trimmed(text):
    """Scope words as said, up to the first word that starts another clause."""
    words = text.split()
    out = words[:2]
    for word in words[2:]:
        bare = word.strip(",.;").lower()
        if bare in _STOP or bare in _OPS or bare in ("no", "not", "keep", "only"):
            break
        out.append(word.strip(",.;"))
    return " ".join(out)


def _op_of(text):
    for word in re.findall(r"[A-Za-z]+", text):
        if word.lower() in _OPS:
            return _OPS[word.lower()], word
    return None, None


def caps_in(text):
    """[{n, unit, unit_words, op, op_word, scope, at, sentence}] for each cap a statement states."""
    found = []
    for piece in _PIECE.finditer(text):
        sentence = piece.group()
        for index, pattern in enumerate(_CAPS):
            for match in pattern.finditer(sentence):
                number, phrase = (
                    (match.group(1), match.group(2))
                    if index == 0
                    else (match.group(2), match.group(1))
                )
                n, words = _number(number), _unit_words(phrase, leading=index != 0)
                if n is None or not words:
                    continue
                op, op_word = _op_of(sentence)
                scope = _SCOPE.search(sentence)
                found.append(
                    {
                        "n": n,
                        "unit": " ".join(words),
                        "unit_words": sorted({singular(w) for w in words if w.lower() not in _OPS}),
                        "op": op,
                        "op_word": op_word,
                        "scope": _trimmed(scope.group()) if scope else None,
                        "at": [piece.start() + match.start(), piece.start() + match.end()],
                        "sentence": sentence.strip(),
                    }
                )
    return found


def _unresolved(words, verb, after):
    """Why a candidate clause is not a supported affirmative report, or None. Words from the
    fixed lists below only; the clause's own text never enters the reason."""
    if after == "?":
        return "question (the clause ends in a question mark)"
    for index, word in enumerate(words):
        if word.endswith("n't"):
            return "negation word (an n't contraction)"
        if word == "up" and words[index + 1 : index + 2] == ["to"]:
            return "limit words up to"
        if word in _CUES:
            return f"{_CUES[word]} word {word}"
    if verb not in _PAST:
        return f"verb form: {verb} is not one of the past forms read as a report"
    return None


def _number_form(token):
    """Why a numeric token is not one whole integer, or None. Fixed words only."""
    if re.fullmatch(r"\d{1,6}", token):
        return None
    if token[0] in "-+−":
        kind = "a signed number"
    elif token.endswith("%"):
        kind = "a percentage"
    elif re.search(r"\d[A-Za-z_]", token):
        kind = "a unit glued to the number"
    elif "/" in token:
        kind = "a fraction"
    elif re.search(r"\d[–—-]\d", token):
        kind = "a range"
    elif ":" in token:
        kind = "a ratio or time"
    elif "." in token:
        kind = "a decimal"
    elif "," in token:
        kind = "a decimal or digit-group separator"
    else:
        kind = "more than six digits"
    return f"number form: {kind}, not one whole integer"


def _unit_ok(words):
    return 1 <= len(words) <= 5 and all(
        w.replace("-", "").isalpha()
        and w not in _STOP
        and w not in _OPS
        and w not in _CUES
        and w not in _BE
        and not w.endswith("ly")
        for w in words
    )


def reports_in(text, unit_words, op):
    """[{n, phrase, verb, shared, form, unresolved, at, sentence}]: each place a text puts a
    number of this unit beside a verb of this operation. Only two forms are reports
    ("form"): NUMBER [of M] UNIT [were|was|have been|has been|had been] PAST-VERB, and
    PAST-VERB NUMBER UNIT, in a clause with no negation, failure, modal, condition, plan or
    limit word and no question mark. Anything else is kept as evidence with "unresolved" set
    to the reason, and contributes nothing to the arithmetic."""
    found = []
    for piece in _PIECE.finditer(text):
        sentence = piece.group()
        after = text[piece.end() : piece.end() + 1]
        plain = sentence.translate(_PLAIN)  # same length: offsets hold in both
        matched = list(_TOKEN.finditer(plain))
        tokens = [(sentence[m.start() : m.end()], m.start(), m.end()) for m in matched]
        words = [m.group().lower() for m in matched]
        said = [t[0] for t in tokens]  # quoted as written
        forms = [_number_form(m.group()) if m.group("num") else None for m in matched]
        for i, word in enumerate(words):
            n = None if forms[i] else _number(word)
            if (n is None and not forms[i]) or (i and words[i - 1] == "of"):
                continue  # "3 of 5 records sent": the 5 is a denominator, not a report
            start = i + 1
            if words[i + 1 : i + 2] == ["of"] and _number((words[i + 2 : i + 3] or [""])[0]):
                start = i + 3  # "3 of 5 records sent" counts the 3
            form, verb, phrase = None, None, []
            for j in range(start, min(start + 8, len(words))):
                if words[j] in _OPS:
                    verb, vi, phrase, at = words[j], j, words[start:j], (start, j)
                    unit = phrase
                    for aux in _AUX:
                        if tuple(phrase[-len(aux) :]) == aux:
                            unit = phrase[: -len(aux)]
                            break
                    if _unit_ok(unit):
                        form = "number unit verb"
                    break
            if verb is None:  # PAST-VERB NUMBER UNIT: the verb just before (fillers between)
                k = i - 1
                while k >= 0 and i - k <= 3 and words[k] in _FILLERS:
                    k -= 1
                if k < 0 or words[k] not in _OPS:
                    continue
                verb, vi, phrase = words[k], k, []
                for w in words[i + 1 : i + 6]:
                    if w in _STOP or w in _OPS or w in _CUES or _number(w) is not None:
                        break
                    if re.match(r"[-+−.]?\d", w):  # another number form
                        break
                    phrase.append(w)
                at = (i + 1, i + 1 + len(phrase))
                if k == i - 1 and _unit_ok(phrase):
                    form = "verb number unit"
            if _OPS[verb] != op:
                continue
            glued = re.search(r"\d([A-Za-z_]\w*)$", word) if forms[i] else None
            named = phrase + [glued.group(1)] if glued else phrase  # "5MB": the unit is glued
            shared = sorted({singular(w) for w in named} & set(unit_words))
            if not shared:
                continue
            why = forms[i] or _unresolved(words, verb, after)
            if why is None and form is None:
                why = "not one of the supported affirmative forms"
            found.append(
                {
                    "n": n,
                    "number_said": said[i],
                    "phrase": " ".join(w for w in said[at[0] : at[1]] if w.lower() not in _BE),
                    "verb": said[vi],
                    "shared": shared,
                    "form": form if why is None else None,
                    "unresolved": why,
                    "at": [piece.start() + tokens[i][1], piece.start() + tokens[i][2]],
                    "sentence": sentence.strip(),
                }
            )
    return found


def _words(text):
    found = _o._tokens(text)
    return None if found is None else [w for w, *_ in found]


def op_of_call(entry):
    """The operation a recorded or proposed call's words name ("send", "delete") or None."""
    tool = str(entry.get("tool") or "")
    if tool not in _SHELLS:
        # A tool of its own (an MCP tool, say) names its operation; a file tool names none.
        if tool in _FILE_TOOLS or not tool:
            return None
        return _named_op(re.split(r"[.:/]", tool)[-1])
    words = _words(str(entry.get("target") or entry.get("text") or ""))
    if not words:
        return None
    words = [w for w in words if not re.match(r"[A-Za-z_]\w*=", w)]
    program = words[0].rsplit("/", 1)[-1].lower() if words else ""
    if program in _DELETE_PROGRAMS or "-delete" in words:
        return "delete"
    if program in _SEND_PROGRAMS:
        return "send"
    if program == "curl":
        sending = ("-T", "--upload-file", "-d", "--data", "-F", "--form", "POST", "PUT")
        return "send" if any(w in sending for w in words) else None
    if program in _INTERPRETERS:  # the script it runs names the operation, by its file name
        script = next((w for w in words[1:] if not w.startswith("-")), "")
        return _named_op(script.rsplit("/", 1)[-1].split(".")[0])
    op = _named_op(program.split(".")[0])
    if op is None and len(words) > 1 and words[1].isalpha():  # a subcommand: "escrowctl send"
        op = _OPS.get(words[1].lower())
    return op


def _named_op(name):
    """The operation a program, script or tool name names by one of its parts."""
    ops = {_OPS.get(part.lower()) for part in re.split(r"[-_]", name)} - {None}
    return ops.pop() if len(ops) == 1 else None


def call_count(arguments, unit_words):
    """(count, how) of what the proposed call's arguments name, or (None, why)."""
    if not isinstance(arguments, dict):
        return None, "arguments not an object"
    for key, value in arguments.items():
        if singular(key) in unit_words:
            if isinstance(value, list):
                return len(value), f"the {len(value)} values of argument {key!r}"
            if isinstance(value, int) and not isinstance(value, bool):
                return value, f"argument {key!r}"
            if isinstance(value, str):
                return 1, f"argument {key!r}"
    command = next(
        (arguments[k] for k in ("command", "cmd") if isinstance(arguments.get(k), str)), None
    )
    if command is None:
        return None, "its arguments state no count"
    words = _words(command)
    if words is None:
        return None, "its command does not tokenize"
    named = [w for w in words if w.startswith("--") and singular(w[2:].split("=")[0]) in unit_words]
    if named:
        return len(named), f"{len(named)} value(s) of option {named[0].split('=')[0]}"
    operands, skip_script, value_next = [], False, False
    words = [w for w in words if not re.match(r"[A-Za-z_]\w*=", w)]
    if words and words[0].rsplit("/", 1)[-1] in _INTERPRETERS:
        skip_script = True
    for word in words[1:]:
        if word in _OPERATORS:
            break
        if value_next:
            value_next = False
            continue
        if word.startswith("--"):
            value_next = "=" not in word
            continue
        if word.startswith("-"):
            continue
        if skip_script:
            skip_script = False
            continue
        if "://" in word:
            continue
        if "/" in word or re.search(r"\.[A-Za-z0-9]{1,5}$", word):
            operands.append(word)
    if not operands:
        return None, "its words name no path operand"
    return len(operands), f"{len(operands)} path operand(s) of its command"


def _clipped(entry):
    text = entry.get("text") or ""
    full = entry.get("text_chars") or entry.get("chars") or 0
    return full > len(text) or text.endswith(_o_clip())


def _o_clip():
    return "…"


def _sessions(mine, others):
    yield "this session", mine, True
    for other in others:
        same = bool(other.get("cwd")) and other.get("cwd") == mine.get("cwd")
        yield other.get("alias") or "?", other, same


def quantities(mine, entry, arguments, others=()):
    """The quantity account for one proposed call: {"version", "caps", "unsupported"}."""
    seq = entry.get("seq")
    statements = []
    for label, session, _ in _sessions(mine, others):
        for s in _o._statements(session, label, seq if label == "this session" else None):
            statements.append(s)
    statements.sort(key=lambda s: (s.get("ts") or 0, s["seq"] or 0))
    caps, unsupported = [], []
    for index, statement in enumerate(statements):
        for cap in caps_in(statement["text"]):
            later = []
            for other in statements[index + 1 :]:
                again = [
                    c
                    for c in caps_in(other["text"])
                    if set(c["unit_words"]) & set(cap["unit_words"])
                ]
                words = {singular(w) for w in re.findall(r"[A-Za-z][\w-]*", other["text"])}
                cue = (
                    _o.cues(other["text"], 0, 0)["cues"].get("revocation")
                    if words & set(cap["unit_words"])
                    else None
                )
                if again or cue:
                    later.append(
                        {
                            "session": other["session"],
                            "seq": other["seq"],
                            "ts": other.get("ts"),
                            "caps": [c["n"] for c in again],
                            "revocation_words": cue or [],
                        }
                    )
            caps.append(
                cap
                | {
                    "session": statement["session"],
                    "seq": statement["seq"],
                    "ts": statement.get("ts"),
                    "field": "prompt",
                    "clipped": statement["clipped"],
                    "later": later,
                }
            )
    for cap in caps:
        cap["contributions"], cap["calls"] = [], []
        if cap["op"] is None:
            cap["not_counted"] = "the cap's sentence names no operation this reads (send, delete)"
            continue
        actions = {}
        for label, session, same in _sessions(mine, others):
            entries = session["summary"].get("entries", ())
            for e in entries:
                if e.get("type") == "action" and e.get("call_id"):
                    actions[(label, e["call_id"])] = e
            for e in entries:
                if label == "this session" and e.get("seq", 0) >= seq:
                    continue
                if e.get("type") == "action" and op_of_call(e) == cap["op"]:
                    status = e.get("status")
                    results = [
                        c
                        for c in entries
                        if c.get("type") == "content" and c.get("call_id") == e.get("call_id")
                    ]
                    cap["calls"].append(
                        {
                            "session": label,
                            "seq": e.get("seq"),
                            "call_id": e.get("call_id"),
                            "status": status,
                            "operands": sorted(_operands(e)),
                            "counted_here": same,
                            "clipped": any(_clipped(c) for c in results),
                            "text": (e.get("target") or e.get("text") or "")[:160],
                        }
                    )
                if e.get("type") != "content" or not isinstance(e.get("text"), str):
                    continue
                for report in reports_in(e["text"], cap["unit_words"], cap["op"]):
                    call = actions.get((label, e.get("call_id")))
                    words = _words(str((call or {}).get("target") or "")) or [""]
                    reader = (call or {}).get("tool") == "Read" or words[0].rsplit("/", 1)[
                        -1
                    ] in _READERS
                    cap["contributions"].append(
                        report
                        | {
                            "session": label,
                            "seq": e.get("seq"),
                            "ts": e.get("ts"),
                            "call_id": e.get("call_id"),
                            "counted_here": same,
                            "clipped": _clipped(e),
                            "of": "text read from a file" if reader else "result of a call",
                            "call_seq": (call or {}).get("seq"),
                            "call_status": (call or {}).get("status"),
                            "call_op": op_of_call(call) if call else None,
                        }
                    )
        cap["contributions"].sort(key=_by_time)
        _bounds(cap)
        count, how = call_count(arguments, cap["unit_words"])
        cap["proposed"] = {"count": count, "how": how}
    shown = caps[-MAX_CAPS:]
    return {
        "version": VERSION,
        "caps": shown,
        "caps_left_out": len(caps) - len(shown),
        "unsupported": sorted(set(unsupported)),
    }


def _by_time(item):
    """Host-time order; anything with no recorded time goes last."""
    ts = item.get("ts")
    return (not isinstance(ts, (int, float)), ts or 0)


def _operands(entry):
    """The path-like operands of a recorded call (not its program or the script an interpreter
    runs): what a retry of it would name again."""
    words = _words(str(entry.get("target") or entry.get("text") or "")) or []
    words = [w for w in words if not re.match(r"[A-Za-z_]\w*=", w)]
    if not words:
        return set()
    rest = words[1:]
    if words[0].rsplit("/", 1)[-1] in _INTERPRETERS:
        script = next((n for n, w in enumerate(rest) if not w.startswith("-")), None)
        rest = rest[script + 1 :] if script is not None else []
    return {w for w in rest if ("/" in w or "." in w) and "://" not in w and not w.startswith("-")}


ASSUMES = (
    "each recorded result reports truthfully what its call did and every report is in it; "
    "calls naming disjoint path operands are separate contributions; nothing else is"
)


def _bounds(cap):
    """Bounds from the affirmative reports only (MR303). At least: the largest single report,
    or, if larger, the sum over groups of calls whose path operands are established disjoint
    (calls sharing an operand form one group and give its largest report). A call naming no
    path operand, a second report in the same result and a report not tied to a call of the
    operation may repeat another, so each enters the lower bound only alone. At most: every
    affirmative report summed; unknown when any candidate is unresolved, a call of the
    operation returned no quantity, was clipped or has no result."""
    counted = [c for c in cap["contributions"] if c["counted_here"]]
    told = [c for c in counted if c["form"]]
    calls = {(c["session"], c["call_id"]): c for c in cap["calls"]}
    by_call, loose = {}, []
    for c in told:
        if c["of"] == "result of a call" and c["call_op"] == cap["op"]:
            by_call.setdefault((c["session"], c["call_id"]), []).append(c["n"])
        else:
            loose.append(c["n"])

    def name(key):
        call = calls.get(key) or {"session": key[0], "seq": "?"}
        return f"{call['session']} #{call['seq']}"

    overlap, groups, nameless = [], [], []
    for key, ns in by_call.items():
        if len(ns) > 1:
            overlap.append(
                f"{name(key)}: {len(ns)} reports in one result may be separate or the same; "
                "the lower bound takes the largest, the upper bound their sum"
            )
        mine = set((calls.get(key) or {}).get("operands") or ())
        if not mine:
            nameless.append(key)
            continue
        joined = [g for g in groups if g["operands"] & mine]
        merged = {"operands": set(mine), "n": max(ns), "keys": [key]}
        for g in joined:
            merged["operands"] |= g["operands"]
            merged["n"] = max(merged["n"], g["n"])
            merged["keys"] = g["keys"] + merged["keys"]
            groups.remove(g)
        groups.append(merged)
    if len(by_call) > 1:
        for key in nameless:
            overlap.append(
                f"{name(key)} names no path operand: whether it repeats another call's units "
                "is not established, so it is added to no other call in the lower bound"
            )
        for g in groups:
            if len(g["keys"]) > 1:
                overlap.append(
                    "calls " + ", ".join(name(k) for k in g["keys"]) + " name a common path "
                    "operand (a retry or resend may repeat): the lower bound takes the largest"
                )
    if loose and told:
        overlap.append(
            f"{len(loose)} report(s) not tied to a call of the operation may repeat another: "
            "each enters the lower bound only alone"
        )
    low = max([sum(g["n"] for g in groups)] + [c["n"] for c in told]) if told else 0
    high = sum(c["n"] for c in told)
    why = []
    for c in counted:
        if c["unresolved"]:
            why.append(
                f"{c['session']} #{c['seq']} report "
                f"{'of a number form' if c['n'] is None else c['n']} not counted: unresolved "
                f"({c['unresolved']})"
            )
    stated = {(c["session"], c["call_id"]) for c in counted}
    for call in cap["calls"]:
        if not call["counted_here"]:
            continue
        key = (call["session"], call["call_id"])
        if call["status"] != "returned":
            why.append(
                f"{call['session']} #{call['seq']} attempted, no result returned ({call['status']})"
            )
        elif call["clipped"]:
            why.append(f"{call['session']} #{call['seq']} result clipped")
        elif key not in stated:
            why.append(f"{call['session']} #{call['seq']} returned with no quantity stated")
    if any(c["clipped"] for c in told):
        why.append("a counted report is clipped")
    cap["total"] = {
        "at_least": low,
        "at_most": None if why else high,
        "unknown_because": why,
        "overlap": overlap,
        "assumes": ASSUMES,
        "calls_with_reports": len(by_call),
        "reports_not_tied_to_a_call": len(loose),
    }


def persisted(account):
    """What a record keeps of the quantities: numbers, operations, references and counts. No unit,
    scope, sentence, phrase, verb or call text: those are session text."""
    caps = []
    for cap in account["caps"]:
        caps.append(
            {
                "n": cap["n"],
                "op": cap["op"],
                "unit_words": len(cap["unit_words"]),
                "scope_stated": cap["scope"] is not None,
                "session": cap["session"],
                "seq": cap["seq"],
                "at": cap["at"],
                "clipped": cap["clipped"],
                "later": [
                    {k: x[k] for k in ("session", "seq", "caps")}
                    | {"revocation_cue": bool(x["revocation_words"])}
                    for x in cap["later"]
                ],
                "contributions": [
                    {
                        k: c[k]
                        for k in (
                            "n",
                            "session",
                            "seq",
                            "call_seq",
                            "call_status",
                            "of",
                            "counted_here",
                            "clipped",
                            "form",
                            "unresolved",
                            "at",
                        )
                    }
                    for c in cap.get("contributions", ())
                ],
                "calls": [
                    {k: c[k] for k in ("session", "seq", "status", "counted_here", "clipped")}
                    for c in cap.get("calls", ())
                ],
                "total": cap.get("total"),
                "proposed": (cap.get("proposed") or {}).get("count"),
            }
        )
    return {
        "version": account["version"],
        "caps": caps,
        "caps_left_out": account["caps_left_out"],
        "caps_shown": account.get("caps_shown"),
        "unsupported": account["unsupported"],
    }


def _place(item):
    return "this session" if item["session"] == "this session" else f"[s:{item['session']}]"


def render(account):
    """[block lines] for the caps, written from the account and from nothing else."""
    blocks = []
    for cap in account["caps"]:
        op = f"{cap['op']} (from {_o._literal(cap['op_word'])})" if cap["op"] else "not stated"
        scope = _o._literal(cap["scope"]) if cap["scope"] else "none"
        lines = [
            f"- cap stated by the user in {_place(cap)} #{cap['seq']} "
            f"(prompt{', clipped' if cap['clipped'] else ''}): number {cap['n']}; unit "
            f"{_o._literal(cap['unit'])}; operation {op}; scope words {scope} (window not "
            f"resolved): {_o._literal(cap['sentence'], _o.SENTENCE_CHARS)}"
        ]
        for later in cap["later"][:MAX_LISTED]:
            what = []
            if later["caps"]:
                numbers = ", ".join(map(str, later["caps"]))
                what.append(f"states another number for a shared unit word: {numbers}")
            if later["revocation_words"]:
                words = ", ".join(later["revocation_words"])
                what.append(f"names the unit with revocation cue words: {words}")
            where = f"{_place(later)} #{later['seq']}"
            lines.append(f"    later user statement {where}: " + "; ".join(what))
        if cap["op"] is None:
            lines.append(f"    nothing counted: {cap['not_counted']}")
            blocks.append(lines)
            continue
        left = len(cap["contributions"]) - MAX_LISTED
        if left > 0:
            lines.append(f"    {left} earlier report(s) not listed")
        for c in cap["contributions"][-MAX_LISTED:]:
            where = f"{_place(c)} #{c['seq']}, {c['of']}"
            if c["call_seq"] is not None:
                where += f" #{c['call_seq']} ({c['call_status']})"
            note = "" if c["counted_here"] else "; another working directory: not counted"
            note += "; clipped" if c["clipped"] else ""
            words = _o._literal(c["phrase"] + " " + c["verb"], 100)
            shared = ", ".join(c["shared"])
            if c["form"]:
                lines.append(
                    f"    reported {c['n']} ({words}; form {c['form']}; unit by shared word "
                    f"{shared}) in {where}{note}"
                )
            else:
                said = _o._literal(c["sentence"], 100)
                number = c["n"] if c["n"] is not None else _o._literal(c["number_said"], 40)
                lines.append(
                    f"    number {number} beside {_o._literal(c['verb'])} in {where}{note}; "
                    f"not counted: unresolved ({c['unresolved']}): {said}"
                )
        if cap["calls"]:
            states = {}
            for call in cap["calls"]:
                states[call["status"]] = states.get(call["status"], 0) + 1
            told = ", ".join(f"{n} {state}" for state, n in sorted(states.items()))
            lines.append(f"    earlier calls of this operation, by their words: {told}")
        total = cap["total"]
        most = total["at_most"]
        if most is None:
            most = "unknown (" + "; ".join(total["unknown_because"][:MAX_LISTED]) + ")"
        for overlap in total["overlap"][:MAX_LISTED]:
            lines.append(f"    overlap: {overlap}")
        lines.append(
            f"    reported before the call: at least {total['at_least']}, at most {most}; "
            f"assuming {total['assumes']}; "
            f"reports tied to {total['calls_with_reports']} call(s) of the operation, "
            f"{total['reports_not_tied_to_a_call']} not tied to one (may repeat another); "
            "reports listed in host-time order; observed effects: none established by this server; "
            "sessions it did not record are "
            "outside both bounds"
        )
        proposed = cap["proposed"]
        if proposed["count"] is None:
            lines.append(f"    the proposed call's arguments: no count ({proposed['how']})")
        else:
            count, low = proposed["count"], total["at_least"]
            lines.append(
                f"    the proposed call's arguments name {count} ({proposed['how']}; whether "
                f"each is one {_o._literal(cap['unit'])} is not established); at least "
                f"{low} + {count} = {low + count} beside the stated number {cap['n']}"
            )
        blocks.append(lines)
    return blocks
