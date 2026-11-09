"""Host-computed path facts, profile armor-hostfacts-paths-v1.

CPU reference. A candidate profile, not a registered serving identity until the trainer registers
it. AgentArmor computes these facts deterministically and passes them to Kai as trusted
``host_computed`` environment facts; Kai is trained to defer to them.

This file is kept byte-identical in two repositories: Kai uses it to build training data, and
AgentArmor uses it as the Python reference for its native C++ port. Both copies and the native port
must give the hand-written results in the shared ``armor-hostfacts-paths-v1/vectors.json``. Its
``MANIFEST.json`` pins this file's SHA-256, so an edit must update both copies, the native port and
the manifest together.

What the host computes for each path effect of the call. The complete effect inventory comes from
the tool schema, never from policy prose or caller claims.

1. Expansion. ``~`` and ``$VAR`` expand only where the actual executor expands them: never for
   direct file APIs or single-quoted shell words, ``$VAR`` only for double-quoted words, and both
   for unquoted words. Anything the reference cannot reproduce exactly refuses: globs, brace
   expansion, command substitution, ``~user``, parameter operators, field splitting, and unset or
   empty variables.
2. Resolution. Relative paths join the trusted working directory. Components resolve one by one:
   ``.`` is dropped, ``..`` pops one resolved component (never above ``/``), and every symlink in
   the host snapshot is followed, with relative targets resolved from the link's parent. The final
   component is followed only for effects whose executor follows a final link: a read or write
   through a link reaches its target, while deleting a link removes the link itself. More than 32
   link expansions refuse, as ELOOP would on macOS.
3. Containment. A policy rule may declare directory roots in its optional ``paths`` field. For
   each path effect the host lists the rules whose roots contain the resolved path (equal to or
   beneath a root, component by component, never by string prefix) and the rules whose roots do
   not. Roots resolve through the same snapshot. A rule whose ``for`` list does not cover the
   effect's operation appears in neither list. Three facts per path effect, whatever the policy's
   size.

Policy authoring stays plain (see ``compile_policy``). A rule adds one optional field, for example
``"paths": {"within": "${workspace}", "for": ["write", "delete"]}``. ``within`` is one root or a
list of roots, written as an absolute path, ``~/...`` or ``${workspace}/...``. ``for`` defaults to
every operation. The rule's description still says what the rule means; the host never reads it.
Policy mistakes are reported once, at load time, with the rule id and a hint.

Facts are measurements and containment lists, never ``rule_matches``, an exception, consent,
authorization or a decision. Unknown, unsupported or inconsistent call inputs raise
:class:`Refusal` with a stable code; they never become a value. Case-insensitive file systems are
supported for ASCII names only (non-ASCII names refuse), and no Unicode normalization is applied.
"""

import re

PROFILE = "armor-hostfacts-paths-v1"
PROVENANCE = "host_computed"
OPERATIONS = ("read", "write", "delete", "list", "execute")
FINAL_LINK = ("follow", "entry")
EXPANSIONS = ("none", "tilde", "shell_unquoted", "shell_double_quoted", "shell_single_quoted")
MAX_LINK_HOPS = 32
MAX_EFFECTS = 8
MAX_PATH_RULES = 32
MAX_ROOTS = 8
MAX_RULES = 256
MAX_PATH = 4096
MAX_LINKS = 256

REFUSAL_CODES = (
    "bad_request",  # malformed request shape, unknown keys or enum values
    "bad_runtime",  # working directory, home, environment or timestamp invalid
    "bad_snapshot",  # link table invalid: non-normalized key, duplicate after folding, empty target
    "bad_path_rules",  # compiled path rules invalid (compile_policy output was not used)
    "empty_path",
    "control_character",  # C0 control or DEL in a path, link, root or value
    "non_ascii_case_insensitive",
    "unknown_home",  # ~ expansion with no trusted home directory
    "unsupported_tilde",  # ~user, ~+, ~-
    "unsupported_expansion",  # $1, $@, ${X:-y}, bare $
    "unset_variable",
    "empty_expansion",  # an unquoted variable expanding to the empty string
    "word_splitting",  # whitespace or glob characters produced or present in an unquoted word
    "glob",
    "shell_metacharacter",  # ; | & < > ( ) or a leading # in an unquoted word: not one word
    "brace_expansion",
    "command_substitution",
    "unsupported_quoting",  # quotes or backslashes inside a word declared as one quoted form
    "trailing_slash_entry",  # an entry (no-follow) effect on a path ending in /
    "dot_final_entry",  # an entry effect whose last component is . or ..
    "link_loop",
    "path_too_long",  # a resolved path longer than MAX_PATH characters
)

POLICY_ERRORS = (
    "bad_policy",  # not a list of rule objects with unique, valid ids
    "bad_paths_field",  # "paths" is not {"within": ..., "for": [...]}
    "bad_root",  # a root that is empty, relative, a glob or uses an unsupported variable
    "unknown_home",  # ~ or ${home} with no known home directory
    "unknown_workspace",  # ${workspace} with no known workspace
    "too_many_path_rules",
)

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_POINTER = re.compile(r"(?:/(?:[^~/\ud800-\udfff]|~[01])*)+")
_RULE_ID = re.compile(r"[^\x00-\x1f\x7f\ud800-\udfff]{1,64}")


class Refusal(ValueError):
    """The host cannot compute the facts; the request takes the host's explicit refusal path."""

    def __init__(self, code, detail):
        assert code in REFUSAL_CODES, code
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class PolicyError(ValueError):
    """A policy file mistake, reported at load time with the rule id and a fix."""

    def __init__(self, code, rule_id, message, hint):
        assert code in POLICY_ERRORS, code
        where = f"rule {rule_id!r}: " if rule_id is not None else ""
        super().__init__(f"{where}{message} ({hint})")
        self.code, self.rule_id, self.message, self.hint = code, rule_id, message, hint


def _require(ok, code, detail):
    if not ok:
        raise Refusal(code, detail)


def _keys(value, required, optional=(), code="bad_request", where="request"):
    _require(type(value) is dict, code, f"{where} must be an object")
    keys = set(value)
    _require(
        set(required) <= keys <= set(required) | set(optional),
        code,
        f"{where} keys must be {sorted(required)} plus optional {sorted(optional)}",
    )


def _text(value, code, where, *, empty_ok=False):
    _require(
        type(value) is str and len(value) <= MAX_PATH,
        code,
        f"{where} must be a string of at most {MAX_PATH} characters",
    )
    _require(
        empty_ok or value != "",
        "empty_path" if code == "bad_request" else code,
        f"{where} is empty",
    )
    _require(
        not any(0xD800 <= ord(ch) <= 0xDFFF for ch in value), code, f"{where} is not valid Unicode"
    )
    _require(
        not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value),
        "control_character",
        f"{where} has a control character",
    )
    return value


def _fold(component, case_sensitive):
    return component if case_sensitive else component.lower()


def _ascii_if_insensitive(value, case_sensitive, where):
    _require(
        case_sensitive or value.isascii(),
        "non_ascii_case_insensitive",
        f"{where}: non-ASCII names on a case-insensitive file system are not supported",
    )


def _components(path):
    return [c for c in path.split("/") if c not in ("", ".")] if path else []


def _is_normalized_absolute(path):
    return (
        path.startswith("/")
        and path != "/"
        and not path.endswith("/")
        and all(c not in ("", ".", "..") for c in path[1:].split("/"))
    )


class Runtime:
    """Trusted runtime state of the executor, from the host.

    Never from the call, a document or the conversation.
    """

    def __init__(self, value):
        _keys(
            value,
            ("cwd", "home", "env", "case_sensitive", "links", "observed_at"),
            code="bad_runtime",
            where="runtime",
        )
        self.case_sensitive = value["case_sensitive"]
        _require(
            type(self.case_sensitive) is bool,
            "bad_runtime",
            "runtime.case_sensitive must be a boolean",
        )
        self.cwd = _text(value["cwd"], "bad_runtime", "runtime.cwd")
        _require(self.cwd.startswith("/"), "bad_runtime", "runtime.cwd must be absolute")
        _ascii_if_insensitive(self.cwd, self.case_sensitive, "runtime.cwd")
        self.home = value["home"]
        if self.home is not None:
            _text(self.home, "bad_runtime", "runtime.home")
            _require(
                self.home.startswith("/"), "bad_runtime", "runtime.home must be absolute or null"
            )
            _ascii_if_insensitive(self.home, self.case_sensitive, "runtime.home")
        env = value["env"]
        _require(
            type(env) is dict and len(env) <= 256,
            "bad_runtime",
            "runtime.env must be an object with at most 256 names",
        )
        for name, text in env.items():
            _require(
                _NAME.fullmatch(name) is not None,
                "bad_runtime",
                f"runtime.env name {name!r} is not a shell name",
            )
            _text(text, "bad_runtime", f"runtime.env[{name}]", empty_ok=True)
        self.env = dict(env)
        self.observed_at = value["observed_at"]
        _require(
            type(self.observed_at) is str and _INSTANT.fullmatch(self.observed_at) is not None,
            "bad_runtime",
            "runtime.observed_at must be an ISO timestamp with seconds and an explicit offset",
        )
        links = value["links"]
        _require(
            type(links) is dict and len(links) <= MAX_LINKS,
            "bad_snapshot",
            f"runtime.links must be an object with at most {MAX_LINKS} links",
        )
        self.links = {}
        for where, target in links.items():
            _text(where, "bad_snapshot", "runtime.links key")
            _require(
                _is_normalized_absolute(where),
                "bad_snapshot",
                f"link location {where!r} must be a normalized absolute path other than /",
            )
            _text(target, "bad_snapshot", f"runtime.links[{where}]")
            _ascii_if_insensitive(where + target, self.case_sensitive, f"runtime.links[{where}]")
            key = tuple(_fold(c, self.case_sensitive) for c in where[1:].split("/"))
            _require(key not in self.links, "bad_snapshot", f"duplicate link location {where!r}")
            self.links[key] = target


def expand(raw, expansion, runtime):
    """The path the executor will see after exactly the expansion it performs, or a Refusal."""
    _require(expansion in EXPANSIONS, "bad_request", f"unknown expansion {expansion!r}")
    _text(raw, "bad_request", "path argument")
    _ascii_if_insensitive(raw, runtime.case_sensitive, "path argument")
    if expansion == "tilde":
        if raw == "~" or raw.startswith("~/"):
            _require(runtime.home is not None, "unknown_home", "~ needs the trusted home directory")
            return runtime.home + raw[1:]
        _require(not raw.startswith("~"), "unsupported_tilde", "~user is not reproduced")
        return raw
    if expansion in ("none", "shell_single_quoted"):
        if expansion == "shell_single_quoted":
            _require(
                "'" not in raw,
                "unsupported_quoting",
                "a single-quoted word cannot contain a single quote",
            )
        return raw
    _require(
        "`" not in raw and "$(" not in raw,
        "command_substitution",
        "command substitution is not reproduced",
    )
    _require(
        "\\" not in raw
        and '"' not in raw
        and (expansion == "shell_double_quoted" or "'" not in raw),
        "unsupported_quoting",
        "embedded quotes and backslashes are not reproduced",
    )
    unquoted = expansion == "shell_unquoted"
    if unquoted:
        _require(
            not any(ch == " " for ch in raw),
            "word_splitting",
            "an unquoted word with whitespace splits",
        )
        _require(
            not any(ch in ";|&<>()" for ch in raw) and not raw.startswith("#"),
            "shell_metacharacter",
            "shell metacharacters mean the adapter did not isolate a single word",
        )
        _require(
            not any(ch in "*?[" for ch in raw),
            "glob",
            "glob characters expand to an unknown file list",
        )
        _require(
            "{" not in raw.replace("${", ""), "brace_expansion", "brace expansion is not reproduced"
        )
    out, i = [], 0
    if unquoted and raw.startswith("~"):
        end = raw.find("/") if "/" in raw else len(raw)
        _require(
            end == 1,
            "unsupported_tilde",
            "only ~ and ~/ expand; ~user, ~+ and ~- are not reproduced",
        )
        _require(
            runtime.home is not None,
            "unknown_home",
            "~ needs the executor's trusted home directory",
        )
        out.append(runtime.home)
        i = 1
    while i < len(raw):
        ch = raw[i]
        if ch != "$":
            out.append(ch)
            i += 1
            continue
        braced = raw.startswith("${", i)
        match = _NAME.match(raw, i + (2 if braced else 1))
        _require(
            match is not None,
            "unsupported_expansion",
            f"unsupported parameter expansion at offset {i}",
        )
        name, end = match.group(), match.end()
        if braced:
            _require(
                raw.startswith("}", end),
                "unsupported_expansion",
                "only ${NAME} braces are reproduced",
            )
            end += 1
        _require(
            name in runtime.env,
            "unset_variable",
            f"${name} is not set in the executor's trusted environment",
        )
        value = runtime.env[name]
        _ascii_if_insensitive(value, runtime.case_sensitive, f"${name}")
        if unquoted:
            _require(
                value != "",
                "empty_expansion",
                f"unquoted ${name} is empty and would remove the word",
            )
            _require(
                not any(ch == " " or ch in "*?[" for ch in value),
                "word_splitting",
                f"unquoted ${name} would split or glob",
            )
        out.append(value)
        i = end
    path = "".join(out)
    _require(path != "", "empty_path", "the expanded path is empty")
    return path


def resolve(path, runtime, *, follow_final):
    """Absolute resolved path of ``path`` against the trusted runtime, component by component."""
    _text(path, "bad_request", "path")
    absolute = path if path.startswith("/") else runtime.cwd.rstrip("/") + "/" + path
    queue = _components(absolute)
    if path.endswith("/"):
        follow_final = True  # a trailing slash forces resolution of the final link, as in POSIX
    result, hops = [], 0
    while queue:
        component = queue.pop(0)
        if component == "..":
            if result:
                result.pop()
            continue
        last = not queue
        key = tuple(_fold(c, runtime.case_sensitive) for c in result + [component])
        target = runtime.links.get(key)
        if target is not None and (follow_final or not last):
            hops += 1
            _require(
                hops <= MAX_LINK_HOPS,
                "link_loop",
                f"more than {MAX_LINK_HOPS} symbolic link expansions",
            )
            if target.startswith("/"):
                result = []
            queue = _components(target) + queue
            continue
        result.append(component)
    out = "/" + "/".join(result)
    _require(len(out) <= MAX_PATH, "path_too_long", f"resolved path exceeds {MAX_PATH} characters")
    return out


def within(target, root, case_sensitive):
    """Equal to or beneath, component by component: ``/data-old`` is not beneath ``/data``."""
    t = [_fold(c, case_sensitive) for c in _components(target)]
    r = [_fold(c, case_sensitive) for c in _components(root)]
    return t[: len(r)] == r


_EXPANSION_NOTE = {
    "none": "no ~ or $VAR expansion (file API)",
    "tilde": "~ expanded to the trusted home directory, no $VAR expansion (file API)",
    "shell_unquoted": (
        "~ and $VAR expanded from the executor's environment as an unquoted shell word"
    ),
    "shell_double_quoted": (
        "$VAR expanded from the executor's environment as a double-quoted shell word; ~ literal"
    ),
    "shell_single_quoted": "no expansion (single-quoted shell word)",
}


def _policy_root(text, rule_id, home, workspace):
    def fail(code, message, hint):
        raise PolicyError(code, rule_id, message, hint)

    if type(text) is not str or text == "" or len(text) > MAX_PATH:
        fail("bad_root", "a root must be a nonempty string", 'for example "${workspace}/data"')
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or 0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        fail("bad_root", f"root {text!r} has a control character", "remove it")
    if any(ch in "*?[" for ch in text):
        fail("bad_root", f"root {text!r} is a glob", "name the directory itself, without wildcards")
    for name, base in (("${home}", home), ("${workspace}", workspace)):
        if text == name or text.startswith(name + "/"):
            if base is None:
                code = "unknown_home" if name == "${home}" else "unknown_workspace"
                fail(code, f"{name} is used but not known", f"configure the {name[2:-1]} directory")
            return base + text[len(name) :]
    if text == "~" or text.startswith("~/"):
        if home is None:
            fail("unknown_home", "~ is used but no home directory is known", "use an absolute path")
        return home + text[1:]
    if text.startswith("~"):
        fail("bad_root", f"root {text!r} names another user's home", "use an absolute path")
    if "$" in text:
        fail("bad_root", f"root {text!r} uses a variable", "only ${workspace} and ${home} expand")
    if not text.startswith("/"):
        fail("bad_root", f"root {text!r} is relative", 'write "${workspace}/' + text + '"')
    return text


def compile_policy(rules, *, home, workspace):
    """Path rules for ``compute`` from AgentArmor policy rules (the parsed JSONL objects).

    Reads only each rule's ``id`` and its optional ``paths`` field; never the description.
    ``"paths": {"within": ROOT | [ROOT, ...], "for": [OPERATION, ...]}``, where ROOT is an absolute
    path, ``~``, ``~/...``, ``${home}/...`` or ``${workspace}/...`` and OPERATION is one of read,
    write, delete, list and execute. ``for`` defaults to all of them. ``home`` and ``workspace`` are
    the trusted absolute directories AgentArmor knows, or None. Raises :class:`PolicyError`.
    """
    for name, base in (("home", home), ("workspace", workspace)):
        if base is not None and (
            type(base) is not str
            or not base.startswith("/")
            or len(base) > MAX_PATH
            or any(ord(c) < 0x20 or ord(c) == 0x7F or 0xD800 <= ord(c) <= 0xDFFF for c in base)
        ):
            raise PolicyError(
                "bad_policy",
                None,
                f"the {name} directory must be an absolute path without control characters",
                "fix the AgentArmor configuration",
            )
    if type(rules) is not list or len(rules) > MAX_RULES:
        raise PolicyError("bad_policy", None, "the policy must be a list of rules", "one per line")
    seen, out = set(), []
    for rule in rules:
        if type(rule) is not dict:
            raise PolicyError("bad_policy", None, "each policy line must be an object", "use {...}")
        rid = rule.get("id")
        if type(rid) is not str or _RULE_ID.fullmatch(rid) is None or rid in seen:
            raise PolicyError(
                "bad_policy", rid, "rule ids must be unique, 1..64 characters", "rename"
            )
        seen.add(rid)
        if "paths" not in rule:
            continue
        paths = rule["paths"]
        if type(paths) is not dict or "within" not in paths or set(paths) - {"within", "for"}:
            extra = sorted(set(paths) - {"within", "for"}) if type(paths) is dict else []
            hint = f"unknown key {extra[0]!r}; " if extra else ""
            raise PolicyError(
                "bad_paths_field",
                rid,
                '"paths" must be {"within": ..., "for": [...]}',
                hint + '"within" is required and "for" is optional',
            )
        roots = paths["within"] if type(paths["within"]) is list else [paths["within"]]
        if not 1 <= len(roots) <= MAX_ROOTS:
            raise PolicyError(
                "bad_paths_field", rid, f'"within" needs 1..{MAX_ROOTS} roots', "list fewer roots"
            )
        ops = paths.get("for", list(OPERATIONS))
        if (
            type(ops) is not list
            or not ops
            or len(set(map(str, ops))) != len(ops)
            or any(o not in OPERATIONS for o in ops)
        ):
            raise PolicyError(
                "bad_paths_field",
                rid,
                '"for" must list distinct operations',
                "use read, write, delete, list or execute, or leave it out for all",
            )
        expanded = [_policy_root(r, rid, home, workspace).rstrip("/") or "/" for r in roots]
        if len(set(expanded)) != len(expanded):
            raise PolicyError("bad_paths_field", rid, "a root is listed twice", "list it once")
        out.append({"rule_id": rid, "roots": expanded, "operations": list(ops)})
    if len(out) > MAX_PATH_RULES:
        raise PolicyError(
            "too_many_path_rules",
            None,
            f"{len(out)} rules declare paths; at most {MAX_PATH_RULES} are supported",
            "merge rules that share roots",
        )
    return out


def _effects(value):
    _require(
        type(value) is list and len(value) <= MAX_EFFECTS,
        "bad_request",
        f"effects must list 0..{MAX_EFFECTS} path effects: the tool schema's complete inventory",
    )
    seen = set()
    for k, effect in enumerate(value, start=1):
        _keys(
            effect,
            ("argument", "raw", "operation", "final_link", "expansion"),
            where=f"effects[{k}]",
        )
        _require(
            type(effect["argument"]) is str
            and _POINTER.fullmatch(effect["argument"]) is not None
            and len(effect["argument"]) <= 256,
            "bad_request",
            f"effects[{k}].argument must be a JSON pointer",
        )
        _require(
            effect["argument"] not in seen,
            "bad_request",
            f"effects[{k}]: duplicate argument pointer",
        )
        seen.add(effect["argument"])
        _require(effect["operation"] in OPERATIONS, "bad_request", f"effects[{k}].operation")
        _require(effect["final_link"] in FINAL_LINK, "bad_request", f"effects[{k}].final_link")
        _require(effect["expansion"] in EXPANSIONS, "bad_request", f"effects[{k}].expansion")
    return value


def _path_rules(value, runtime):
    _require(
        type(value) is list and len(value) <= MAX_PATH_RULES,
        "bad_path_rules",
        f"path_rules must list at most {MAX_PATH_RULES} rules (compile_policy output)",
    )
    ids = set()
    for j, r in enumerate(value, start=1):
        _keys(
            r, ("rule_id", "roots", "operations"), code="bad_path_rules", where=f"path_rules[{j}]"
        )
        _require(
            type(r["rule_id"]) is str
            and _RULE_ID.fullmatch(r["rule_id"]) is not None
            and r["rule_id"] not in ids,
            "bad_path_rules",
            f"path_rules[{j}].rule_id must be unique, 1..64 characters without controls",
        )
        ids.add(r["rule_id"])
        roots = r["roots"]
        _require(
            type(roots) is list and 1 <= len(roots) <= MAX_ROOTS,
            "bad_path_rules",
            f"path_rules[{j}].roots must list 1..{MAX_ROOTS} roots",
        )
        for root in roots:
            _text(root, "bad_path_rules", f"path_rules[{j}].roots")
            _require(root.startswith("/"), "bad_path_rules", f"path_rules[{j}] root not absolute")
            _ascii_if_insensitive(root, runtime.case_sensitive, f"path_rules[{j}].roots")
        ops = r["operations"]
        _require(
            type(ops) is list
            and ops
            and len(set(map(str, ops))) == len(ops)
            and all(o in OPERATIONS for o in ops),
            "bad_path_rules",
            f"path_rules[{j}].operations must be a nonempty list of distinct operations",
        )
    return value


def compute(request):
    """The facts for one call in a fixed order, {"profile", "status": "ok", "facts"}, or a Refusal.

    Per path effect ``k`` in inventory order: ``call_path_<k>_resolved``; then, when the policy has
    path rules, ``call_path_<k>_inside_roots_of`` and ``call_path_<k>_outside_roots_of``, each a
    list of rule ids in policy order. ``path_rules`` is ``compile_policy`` output.
    """
    _keys(request, ("profile", "runtime", "effects", "path_rules"))
    _require(request["profile"] == PROFILE, "bad_request", f"profile must be {PROFILE}")
    runtime = Runtime(request["runtime"])
    effects = _effects(request["effects"])
    rules = _path_rules(request["path_rules"], runtime)
    case = "case-sensitive" if runtime.case_sensitive else "ASCII case-insensitive"
    roots = {
        r["rule_id"]: [resolve(x, runtime, follow_final=True) for x in r["roots"]] for r in rules
    }

    def fact(name, value, note):
        return {
            "name": name,
            "value": value,
            "unit": None,
            "provenance": PROVENANCE,
            "as_of": runtime.observed_at,
            "note": note,
        }

    facts = []
    for k, e in enumerate(effects, start=1):
        follow = e["final_link"] == "follow"
        path = expand(e["raw"], e["expansion"], runtime)
        if not follow:
            last = [c for c in path.split("/") if c != ""]
            _require(
                not path.endswith("/"), "trailing_slash_entry", "an entry effect cannot end in /"
            )
            _require(
                not last or last[-1] not in (".", ".."),
                "dot_final_entry",
                "an entry effect cannot end in . or ..",
            )
        target = resolve(path, runtime, follow_final=follow)
        facts.append(
            fact(
                f"call_path_{k}_resolved",
                target,
                f"Host resolution of call argument {e['argument']} for {e['operation']}: "
                f"working directory {runtime.cwd}, {_EXPANSION_NOTE[e['expansion']]}, "
                "symbolic links from the host snapshot, "
                f"final link {'followed' if follow else 'not followed'}.",
            )
        )
        if not rules:
            continue
        selected = [r["rule_id"] for r in rules if e["operation"] in r["operations"]]
        inside = [
            rid
            for rid in selected
            if any(within(target, x, runtime.case_sensitive) for x in roots[rid])
        ]
        tail = (
            f"Rules whose paths field does not cover {e['operation']} are in neither list. "
            "Containment only: not a rule match, exception or decision."
        )
        facts.append(
            fact(
                f"call_path_{k}_inside_roots_of",
                inside,
                f"Policy rules with a declared root that contains call_path_{k}_resolved "
                f"(equal or beneath, component by component, {case}; roots resolved through the "
                f"same symbolic links). {tail}",
            )
        )
        facts.append(
            fact(
                f"call_path_{k}_outside_roots_of",
                [rid for rid in selected if rid not in inside],
                "Policy rules none of whose declared roots contains "
                f"call_path_{k}_resolved. {tail}",
            )
        )
    return {"profile": PROFILE, "status": "ok", "facts": facts}


def compute_or_refusal(request):
    """``compute`` with refusals as data: {"profile", "status": "refused", "code", "detail"}."""
    try:
        return compute(request)
    except Refusal as exc:
        return {"profile": PROFILE, "status": "refused", "code": exc.code, "detail": exc.detail}
