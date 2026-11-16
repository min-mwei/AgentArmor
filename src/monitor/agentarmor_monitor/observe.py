"""Session observations: comparisons this server computes for one proposed call. Standard library.

Version agentarmor-observations/v1 (VERSION). Off unless the operator starts the server with
`serve --session-observations on`. Research and shadow: nothing here is qualified, and every
record keeps policy_ready false.

Why. A measurement of the unchanged decision model (K0) behind the Summarizer found that most
attacks it allowed had the decisive evidence in its input, with the decisive comparison implicit:
the call's target differs by a character or two from the one the user named, possibly in another
session. K0 decides well when such a comparison is stated. This module states it, as a typed
observation with its sources, and never as a verdict (MR296 section 3, MR297 priority 1).

Where it is computed. On the server, in escalate.detect(), for a decision request only (a
proposed call), from what the server owns: the call's arguments as the request shows them
(escalate.proposed(): the caller's, or the hook's log, redacted), this session's summary up to
the call, and the other sessions the earlier-sessions lookup reads, each cut at the call's time by
the lookup's own rule (summarizer.history.cut_at). Provenance is the hook's: a statement is the
user's only when it is a prompt entry Summarizer recorded with trust "user"; nothing a call,
a tool result, a gate, a client, a dataset label or a scenario name says is read as one.

What it computes (A, target comparisons). Targets are extracted from the call's arguments by a
small fixed grammar: URLs (and their hosts), e-mail addresses (accounts), host names, `host:path`
copies, paths, and the values of options that name a destination, repository or account. The
same grammar reads every user-channel statement before the call. Each call target is compared
with the user-named targets of the same kind: raw code-point equality; otherwise the differing
characters (position, code points) when the two are near (case only, a confusable letter, an
edit distance of at most two, or one a dot-label or path-component extension of the other). With
each user-named value come where it was said (session alias, event, field, offsets), whether the
statement was clipped, and the words of that sentence that commonly carry a grant, a limit, a
negation, a condition, a scope, a revocation or a back-reference, and whether the name sat in
quotation marks. Those are cue words found, never a reading: "user mentioned X" is not "user
authorized X". Every later user statement naming the same value is listed with its own cues, so a
later revocation stays visible. Semantic identity is always reported as unknown, with the reasons
that apply (case rules, IDNA and confusables, aliases, symlinks, working directory); raw
inequality is never object inequality, and no observation denies, allows or ranks anything.

What it does not do. It does not parse authority, resolve pronouns, count quantities (B is a
later, separate part), read files, resolve DNS or the filesystem, or touch src/hostfacts or the
host-fact profile. facts_sufficient is not consulted and nothing here certifies history.

How it is rendered. policy.layout() places one labelled section, "Session observations", after
the host facts and before the conversation, as a piece of kind "observation". The packet is
built in the same budget as without observations, so every original line (grants, restrictions,
outcomes) and the proposed call are byte-for-byte what they would be with the option off; the
section is extra and is capped at MAX_CHARS, past which comparisons are left out and counted.
Session strings in it are single-line JSON literals. The section copies session text, so a span
the decision model places in it is reported unavailable (escalate.spans_record): no source map
is claimed for it.

Coverage. The account counts the statements read and clipped, the sessions read, the call's
targets by kind, the comparisons shown and left out, and every extraction it could not make
(unsupported: a command that does not tokenize, a call shown only as a display text, a value
that is not a string). An observation over a partial view says so in its own coverage line.
"""

import difflib
import posixpath
import re
import shlex
import unicodedata

VERSION = "agentarmor-observations/v1"
TITLE = (
    f"Session observations ({VERSION}; computed by this server from the recorded session; "
    "comparisons, not verdicts):"
)
MAX_CHARS = 2400  # the section's cap; what does not fit is left out and counted
MAX_COMPARISONS = 6
MAX_MENTIONS = 3  # user statements listed for one user-named value
SENTENCE_CHARS = 180

KINDS = ("url", "host", "account", "path", "repository")
# Options whose value names where something goes or whose it is. The kind is the option's.
_OPTION_KINDS = {
    "--to": "account",
    "--recipient": "account",
    "--account": "account",
    "--user": "account",
    "--owner": "account",
    "--repo": "repository",
    "--repository": "repository",
    "--remote": "repository",
    "--host": "host",
    "--dest": "path",
    "--destination": "path",
    "--bucket": "repository",
}
_PATH_KEYS = frozenset({"file_path", "path", "notebook_path", "filename", "directory", "dir"})
_URL_KEYS = frozenset({"url", "uri", "endpoint"})
_COMMAND_KEYS = frozenset({"command", "cmd", "script"})
# Free text a call writes or searches for: what it says is content, not where the call goes.
_TEXT_KEYS = frozenset(
    "content new_string old_string text body message description prompt query pattern edits "
    "summary title comment notes".split()
)
# Keys whose value, when the grammar finds nothing in it, is still taken whole as a target.
_NAMED_KEYS = {
    "to": "account",
    "recipient": "account",
    "recipients": "account",
    "account": "account",
    "user": "account",
    "repo": "repository",
    "repository": "repository",
    "remote": "repository",
    "destination": "path",
    "dest": "path",
    "target": "path",
    "host": "host",
}
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>`]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LABEL = r"[^\W_](?:[\w-]*[^\W_])?"
_HOST = re.compile(rf"(?:{_LABEL}\.)+{_LABEL}")
_COPY = re.compile(rf"((?:{_LABEL}\.)+{_LABEL}):(\S*)")
_PATH = re.compile(r"(?:~|\.{1,2})?/?[\w.~@+-]+(?:/[\w.~@+-]*)+|~/?[\w.~@+-]*")
# Endings that mark a file name, not a host ("audio-index.json"). A host whose last label is one
# of these is read as a path-like name and is not compared as a host.
_FILE_ENDINGS = frozenset(
    "json jsonl txt md py sh js ts yml yaml toml cfg conf ini csv tsv log tgz gz zip tar bz2 xz "
    "pdf png jpg jpeg gif svg html htm xml sql db sqlite lock pem key crt env out bin so dylib "
    "rb pl go rs c h cpp java kt swift ipynb bak tmp old sha256 sig asc".split()
)
_TRAIL = ".,;:!?)]}>\"'`’”"

# Cue words, by what they commonly carry. Found words only: never an interpretation.
_CUES = (
    ("negation", r"not|never|no|don't|do not|without|except|nor|neither"),
    ("limit", r"only|no more than|at most|up to|cap|limit|maximum|exclusively|solely"),
    ("condition", r"if|unless|until|after|once|when|provided|before"),
    ("scope", r"across|during|within|for this|per|throughout"),
    (
        "revocation",
        r"revoke[sd]?|withdraw[ns]?|withdrew|cancel(?:led|ed)?|no longer|instead|stop"
        r"|rescind(?:ed)?",
    ),
    ("grant", r"allow(?:ed)?|approve[sd]?|authori[sz]e[sd]?|permit(?:ted)?|may|ok(?:ay)?"),
    ("reference", r"same|that|previous|usual|earlier|above|it|there"),
)
_CUES = tuple((name, re.compile(rf"\b(?:{words})\b", re.I)) for name, words in _CUES)
_SENTENCE = re.compile(r"(?:[^.;!?\n]|[.;!?](?=\S))+(?:[.;!?]+|$)")
_QUOTES = (('"', '"'), ("'", "'"), ("`", "`"), ("“", "”"), ("‘", "’"))

# A few letters that look like ASCII letters in common fonts (Cyrillic, Greek). The skeleton is a
# hint that two names look alike; it is never taken to make them the same.
_CONFUSABLE = dict(
    zip(
        "аеорсухіјѕԁԛԝАВЕКМНОРСТХІЈЅοαιкνρτυχΑΒΕΖΗΙΚΜΝΟΡΤΥΧ",
        "aeopcyxijsdqwABEKMHOPCTXIJSoaikvptuxABEZHIKMNOPTYX",
        strict=True,
    )
)


_COMPATIBLE = {
    "path": ("path", "repository"),
    "repository": ("repository", "path"),
    "host": ("host",),
    "url": ("url",),
    "account": ("account",),
}
_WORDISH = re.compile(r"[^\W_][\w.@/+-]*")


def _strip(value):
    return value.rstrip(_TRAIL)


def _is_host(value):
    if not _HOST.fullmatch(value) or value.count(".") < 1:
        return False
    last = value.rsplit(".", 1)[1]
    return last.lower() not in _FILE_ENDINGS and not last.isdigit() and len(last) >= 2


def _url_host(url):
    rest = url.split("://", 1)[1]
    rest = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    rest = rest.rsplit("@", 1)[-1]
    return rest.split(":", 1)[0]


def targets_in(text, field, start_offset=0):
    """[(kind, value, binding)] for the targets a piece of prose names, in order of place.

    binding is {"field", "at": [start, end]} in the text given (plus start_offset)."""
    found, taken = [], []

    def free(a, b):
        return all(b <= x or a >= y for x, y in taken)

    def add(kind, value, a, b):
        found.append((kind, value, {"field": field, "at": [a + start_offset, b + start_offset]}))

    for match in _URL.finditer(text):
        value = _strip(match.group())
        a, b = match.start(), match.start() + len(value)
        add("url", value, a, b)
        host = _url_host(value)
        if host:
            at = text.find(host, a, b)
            add("host", host, at, at + len(host))
        taken.append((a, b))
    for match in _EMAIL.finditer(text):
        a, b = match.span()
        if free(a, b):
            add("account", match.group(), a, b)
            taken.append((a, b))
    for match in _COPY.finditer(text):
        a, b = match.span()
        if free(a, b) and _is_host(match.group(1)) and match.group(2).startswith(("/", "~")):
            add("host", match.group(1), a, match.end(1))
            path = _strip(match.group(2))
            if len(path) > 1:
                add("path", path, match.start(2), match.start(2) + len(path))
            taken.append((a, b))
    for match in _HOST.finditer(text):
        a, b = match.span()
        if not free(a, b) or (a and text[a - 1] in "/~.@-_"):
            continue
        if b < len(text) and text[b] in "/":
            continue
        value = match.group()
        if _is_host(value):
            add("host", value, a, b)
            taken.append((a, b))
    for match in _PATH.finditer(text):
        value = _strip(match.group())
        a, b = match.start(), match.start() + len(value)
        if free(a, b) and "/" in value and len(value) > 1 and not value.startswith("//"):
            add("path", value, a, b)
            taken.append((a, b))
    found.sort(key=lambda item: (item[2]["at"][0], KINDS.index(item[0])))
    return found


def _tokens(command):
    """[(token, start, end)] of a shell command, or None when it does not tokenize."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return None
    out, cursor = [], 0
    for word in words:
        at = command.find(word, cursor)
        if at < 0:  # a quoted word: its text is not in the command as written
            out.append((word, None, None))
            continue
        out.append((word, at, at + len(word)))
        cursor = at + len(word)
    return out


def call_targets(arguments, unsupported):
    """[(kind, value, binding)] of the proposed call's arguments (a dict as the request shows it).

    binding: {"field": "arguments.<key>", "token": n (command words), "at": [start, end] or
    None}. What cannot be read is added to unsupported."""
    found = []
    if not isinstance(arguments, dict):
        unsupported.append("arguments_not_an_object")
        return found
    for key in sorted(arguments):
        value, field = arguments[key], f"arguments.{key}"
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            value = shlex.join(value) if key in _COMMAND_KEYS else None
            if value is None:
                continue
        if not isinstance(value, str):
            continue
        name = key.lower()
        if name in _COMMAND_KEYS:
            words = _tokens(value)
            if words is None:
                unsupported.append("command_untokenized")
                found += targets_in(value, field)
                continue
            option = None
            for index, (word, a, b) in enumerate(words):
                if word.startswith("--") and "=" in word:
                    option, word = word.split("=", 1)
                    a = None if a is None else a + len(option) + 1
                kind_by_option = _OPTION_KINDS.get(option) if option else None
                option = word if word.startswith("-") else None
                if word.startswith("-"):
                    continue
                here = targets_in(word, field, a or 0)
                if a is None:
                    here = [(k, v, {"field": field, "at": None}) for k, v, _ in here]
                for kind, val, binding in here:
                    found.append((kind, val, binding | {"token": index}))
                if kind_by_option and not here and word:
                    at = None if a is None else [a, b]
                    found.append((kind_by_option, word, {"field": field, "at": at, "token": index}))
        elif name in _PATH_KEYS:
            found.append(("path", value, {"field": field, "at": [0, len(value)]}))
        elif name in ("input", "patch") and _PATCH_FILE.search(value):
            for match in _PATCH_FILE.finditer(value):
                found.append(
                    ("path", match.group(1).strip(), {"field": field, "at": list(match.span(1))})
                )
        elif name not in _TEXT_KEYS:
            here = targets_in(value, field)
            if not here and name in _NAMED_KEYS and value.strip() and "\n" not in value:
                here = [(_NAMED_KEYS[name], value, {"field": field, "at": [0, len(value)]})]
            elif name in ("repo", "repository") and len(here) == 1 and here[0][0] == "path":
                here = [("repository", *here[0][1:])]  # the key says what the slash name is
            found += here
    return found


def _skeleton(text):
    return "".join(_CONFUSABLE.get(c, c) for c in unicodedata.normalize("NFKC", text)).casefold()


def _distance(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _joined(path, cwd):
    """A relative path joined to a recorded working directory, or None when either is missing."""
    if not path or path.startswith(("/", "~")) or not cwd or not cwd.startswith("/"):
        return path if path.startswith("/") else None
    return posixpath.normpath(posixpath.join(cwd, path))


def relation(kind, call, user):
    """How a call's value stands to a user-named value of the same kind, or None when not near.

    One of: "equal" (same code points), "case_only", "confusable" (same skeleton), "extends"
    (the call's value is the user's plus labels or components), "within" (the other way), or
    "near" (edit distance at most two). Raw text only; nothing about the objects they name."""
    if call == user:
        return "equal"
    if call.casefold() == user.casefold():
        return "case_only"
    if _skeleton(call) == _skeleton(user):
        return "confusable"
    if kind in ("host", "path", "url", "repository"):
        # One is the other with more labels, components or a suffix after a separator.
        seps = ".-" if kind == "host" else "/.-_"
        for longer, shorter, name in ((call, user, "extends"), (user, call, "within")):
            if len(longer) > len(shorter) and shorter:
                if longer.startswith(shorter) and longer[len(shorter)] in seps:
                    return name
                if longer.endswith(shorter) and longer[-len(shorter) - 1] in seps:
                    return name
    if min(len(call), len(user)) >= 5 and _distance(call, user, 2) <= 2:
        return "near"
    return None


def differences(call, user, limit=6):
    """The differing characters: [{op, at (in the call's value), call, user}] with code points."""
    out = []
    matcher = difflib.SequenceMatcher(a=call, b=user, autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "equal":
            continue
        out.append({"op": op, "at": a1, "call": call[a1:a2], "user": user[b1:b2]})
        if len(out) >= limit:
            break
    return out


def _points(text):
    return " ".join(f"U+{ord(c):04X}" for c in text) or "nothing"


def unknowns(kind, call, user, rel, same_session):
    """Why the objects the two values name cannot be called the same or different here."""
    why = []
    if kind in ("host", "url", "account"):
        why.append("name aliases and ownership not established")
        if any(ord(c) > 127 for c in call + user):
            why.append("IDNA and confusable mapping not established")
    if kind == "path":
        why.append("filesystem case rules and symlinks not established")
        if not (call.startswith("/") and user.startswith("/")):
            why.append("working directory not established" if not same_session else "relative path")
        if not same_session:
            why.append("namespace of the earlier session unknown")
    if kind == "repository":
        why.append("repository aliases and remotes not established")
    if rel == "case_only" and kind != "path":
        why.append("case rules of this namespace not established")
    return why


def _sentence(text, at):
    for match in _SENTENCE.finditer(text):
        if match.start() <= at < max(match.end(), match.start() + 1):
            return match.start(), match.end()
    return 0, len(text)


def cues(text, start, end):
    """The cue words of the sentence that holds text[start:end], by kind, and whether the value
    stood in quotation marks there. Words found, in order; never a reading of them."""
    a, b = _sentence(text, start)
    sentence = text[a:b]
    found = {}
    for name, pattern in _CUES:
        words = [m.group().lower() for m in pattern.finditer(sentence)]
        if words:
            found[name] = words
    quoted = any(
        text.rfind(left, a, start) > text.rfind(right, a, start)
        if left != right
        else text.count(left, a, start) % 2 == 1
        for left, right in _QUOTES
    )
    return {"cues": found, "quoted": quoted, "sentence": [a, b]}


def _statements(session, label, before_seq=None):
    """User-channel statements of one session: prompt entries recorded with trust "user"."""
    out = []
    for entry in session["summary"].get("entries", ()):
        if entry.get("type") != "prompt" or entry.get("trust") != "user":
            continue
        if before_seq is not None and entry.get("seq", 0) >= before_seq:
            continue
        text = entry.get("text")
        if not isinstance(text, str):
            continue
        chars = entry.get("chars")
        out.append(
            {
                "session": label,
                "seq": entry.get("seq"),
                "ts": entry.get("ts"),
                "text": text,
                "clipped": isinstance(chars, int) and chars > len(text),
                "cwd": session.get("cwd"),
            }
        )
    return out


def observe(mine, entry, arguments, call_source, others=()):
    """The observation account for one proposed call, or None when the call has no arguments.

    mine: this session as the lookup takes it ({"key", "summary", "cwd", "alias", ...}); entry:
    the call's summary entry; arguments: the call's arguments as the request shows them (a dict),
    with call_source as escalate.proposed() names it; others: the other sessions as they stood at
    the call's time (history.cut_at), oldest first. Everything returned is data; render() writes
    the section from it and from nothing else."""
    unsupported = []
    if call_source == "representation":
        unsupported.append("call_shown_as_display_text")
    targets = call_targets(arguments, unsupported)
    seq = entry.get("seq")
    said = _statements(mine, "this session", seq)
    for other in others:
        said += _statements(other, other.get("alias") or "?")
    named = []  # (kind, value, statement, binding, cues)
    for statement in said:
        for kind, value, binding in targets_in(statement["text"], "prompt"):
            a, b = binding["at"]
            named.append((kind, value, statement, binding, cues(statement["text"], a, b)))
    comparisons, seen = [], set()
    nothing_near = {kind: 0 for kind in KINDS}
    for kind, value, binding in targets:
        if (kind, value) in seen:
            continue
        seen.add((kind, value))
        same_kind = [item for item in named if item[0] in _COMPATIBLE[kind]]
        if kind in ("account", "repository", "host") and " " not in value:
            # A name taken whole from a named argument ("acct-…") is also looked for as a word
            # of each statement: the grammar above has no shape for a bare account name.
            for statement in said:
                for match in _WORDISH.finditer(statement["text"]):
                    word = _strip(match.group())
                    taken = any(
                        item[2] is statement
                        and item[3]["at"][0] <= match.start() < item[3]["at"][1]
                        for item in same_kind
                    )
                    if not taken and relation(kind, value, word) is not None:
                        a, b = match.start(), match.start() + len(word)
                        place = {"field": "prompt", "at": [a, b]}
                        same_kind.append(
                            (kind, word, statement, place, cues(statement["text"], a, b))
                        )
        near = {}
        for _, user_value, statement, place, found in same_kind:
            rel, compared = relation(kind, value, user_value), (value, user_value)
            if kind == "path" and rel not in ("equal",):
                # Each relative path against its own session's recorded working directory: a
                # comparison of the joined text, said to be one, never of the files.
                a, b = _joined(value, mine.get("cwd")), _joined(user_value, statement["cwd"])
                if a and b and (a, b) != (value, user_value):
                    joined = relation(kind, a, b)
                    if joined is not None and (rel is None or joined == "equal"):
                        rel, compared = f"{joined}_after_joining_working_directories", (a, b)
            if rel is not None:
                held = near.setdefault(user_value, (rel, compared, []))
                held[2].append((statement, place, found))
        if not near:
            if kind == "path":
                nothing_near[kind] += 1
                continue
            distinct = sorted({item[1] for item in same_kind})
            comparisons.append(
                {
                    "kind": kind,
                    "call": value,
                    "binding": binding,
                    "candidates": [],
                    "user_named_of_kind": distinct,
                }
            )
            continue
        candidates = []
        for user_value, (rel, compared, mentions) in near.items():
            same_session = all(m[0]["session"] == "this session" for m in mentions)
            candidates.append(
                {
                    "user": user_value,
                    "raw_equal": rel == "equal",
                    "relation": rel,
                    "compared": list(compared) if compared != (value, user_value) else None,
                    "differences": differences(*compared) if not rel.startswith("equal") else [],
                    "object_identity": "unknown",
                    "unknown_because": unknowns(kind, value, user_value, rel, same_session),
                    "mentions": [
                        {
                            "session": s["session"],
                            "seq": s["seq"],
                            "field": place["field"],
                            "at": place["at"],
                            "clipped": s["clipped"],
                            "namespace": "this session"
                            if s["session"] == "this session"
                            else "unknown",
                            "cues": found["cues"],
                            "quoted": found["quoted"],
                            "sentence": s["text"][
                                found["sentence"][0] : found["sentence"][1]
                            ].strip(),
                        }
                        for s, place, found in mentions
                    ],
                }
            )
        candidates.sort(key=lambda c: (not c["raw_equal"], c["user"]))
        comparisons.append(
            {"kind": kind, "call": value, "binding": binding, "candidates": candidates}
        )
    counts = {kind: sum(1 for k, *_ in targets if k == kind) for kind in KINDS}
    return {
        "version": VERSION,
        "cutoff": {
            "session": mine["summary"].get("session", {}).get("session_id"),
            "before_seq": seq,
            "others_as_of": entry.get("ts"),
        },
        "call_source": call_source,
        "targets": comparisons,
        "coverage": {
            "statements_read": len(said),
            "statements_clipped": sum(s["clipped"] for s in said),
            "sessions_read": 1 + len(others),
            "call_targets": {k: v for k, v in counts.items() if v},
            "call_paths_with_no_near_user_path": nothing_near["path"],
            "user_named_targets": len(named),
        },
        "unsupported": sorted(set(unsupported)),
    }


def _literal(text, limit=None):
    import json

    if limit is not None and len(text) > limit:
        text = text[:limit] + "…"
    return json.dumps(text, ensure_ascii=False)


def _where(binding):
    out = binding["field"]
    if "token" in binding:
        out += f" word {binding['token']}"
    return out


def _mention_line(mention, printed):
    where = "this session" if mention["session"] == "this session" else f"[s:{mention['session']}]"
    key = (mention["session"], mention["seq"], mention["sentence"])
    words = "; ".join(f"{k} {', '.join(dict.fromkeys(v))}" for k, v in mention["cues"].items())
    out = f"      said by the user in {where} #{mention['seq']} ({mention['field']}"
    out += ", clipped" if mention["clipped"] else ""
    out += ", in quotation marks" if mention["quoted"] else ""
    out += f"); cue words in that sentence: {words or 'none'}"
    if key in printed:
        return out + " (sentence quoted above)"
    printed.add(key)
    return out + ": " + _literal(mention["sentence"], SENTENCE_CHARS)


def render(account):
    """The section's lines, written from the account and from nothing else, within MAX_CHARS."""
    cutoff = account["cutoff"]
    head = [
        TITLE,
        f"cutoff: this session before #{cutoff['before_seq']}; other sessions as they stood at the "
        "call's time. A user naming a value is not the user authorizing it; object identity is "
        "never established here.",
    ]
    blocks, printed = [], set()

    def rank(item):  # room goes first to a near difference, then to equality, then the rest
        candidates = item["candidates"]
        if any(not c["raw_equal"] for c in candidates):
            return 0
        return 1 if candidates else 2

    for item in sorted(account["targets"], key=rank):
        call = f"- call {item['kind']} {_literal(item['call'])} ({_where(item['binding'])})"
        if not item["candidates"]:
            named = item.get("user_named_of_kind") or []
            if named:
                shown = ", ".join(_literal(v, 80) for v in named[:3])
                more = f" and {len(named) - 3} more" if len(named) > 3 else ""
                blocks.append(
                    [
                        f"{call}: no user-named {item['kind']} is equal or near; "
                        f"the user named {shown}{more}"
                    ]
                )
            else:
                blocks.append(
                    [f"{call}: no user-channel statement before the call names a {item['kind']}"]
                )
            continue
        lines = [f"{call}: {len(item['candidates'])} user-named candidate(s)"]
        for candidate in item["candidates"]:
            if candidate["raw_equal"]:
                said = f"    raw text equal to user-named {_literal(candidate['user'])}"
            else:
                diffs = "; ".join(
                    f"at {d['at']} call {_literal(d['call'])} ({_points(d['call'])}) vs user "
                    f"{_literal(d['user'])} ({_points(d['user'])})"
                    for d in candidate["differences"]
                )
                said = f"    raw text differs from user-named {_literal(candidate['user'])}"
                if candidate.get("compared"):
                    a, b = candidate["compared"]
                    said += (
                        f"; joined to recorded working directories: {_literal(a)} vs {_literal(b)}"
                    )
                said += f" ({candidate['relation'].replace('_', ' ')})" + (
                    f": {diffs}" if diffs else ""
                )
            said += f"; object identity unknown ({', '.join(candidate['unknown_because'])})"
            lines.append(said)
            lines += [_mention_line(m, printed) for m in candidate["mentions"][:MAX_MENTIONS]]
            if len(candidate["mentions"]) > MAX_MENTIONS:
                lines.append(
                    f"      and {len(candidate['mentions']) - MAX_MENTIONS} later mention(s) "
                    "not shown"
                )
        blocks.append(lines)
    coverage = account["coverage"]
    tail = (
        f"coverage: {coverage['statements_read']} user statements read in "
        f"{coverage['sessions_read']} "
        f"sessions ({coverage['statements_clipped']} clipped); call targets "
        + (", ".join(f"{k} {v}" for k, v in coverage["call_targets"].items()) or "none")
        + "; call paths with no near user-named path "
        + f"{coverage['call_paths_with_no_near_user_path']}"
    )
    if account["unsupported"]:
        tail += "; not read: " + ", ".join(account["unsupported"])
    room = MAX_CHARS - sum(len(line) + 1 for line in head) - len(tail) - 60
    kept, left = [], 0
    for block in blocks[:MAX_COMPARISONS]:
        need = sum(len(line) + 1 for line in block)
        if need <= room:
            kept += block
            room -= need
        else:
            left += 1
    left += max(0, len(blocks) - MAX_COMPARISONS)
    if left:
        tail += f"; comparisons left out for room: {left}"
    account["coverage"]["comparisons_shown"] = len(blocks) - left
    account["coverage"]["comparisons_left_out"] = left
    return head + kept + [tail]
