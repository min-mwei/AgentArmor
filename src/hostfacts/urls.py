"""Host-computed URL facts, profile armor-hostfacts-urls-v1.

CPU reference. A candidate profile, not a registered serving identity until the trainer registers
it. AgentArmor computes these facts deterministically and passes them to Kai as trusted
``host_computed`` environment facts; Kai is trained to defer to them. Standard library only
(``ipaddress``, ``re`` and the ``idna`` codec). No network: no DNS lookup, no redirect, no fetch.

Like ``paths.py`` and ``measures.py``, this file is meant to be kept byte-identical in Kai
(``train/kai/hostfacts/urls.py``) and AgentArmor (``src/hostfacts/urls.py``). Both copies must give
the hand-written results in the shared ``armor-hostfacts-urls-v1/vectors.json``, whose
``MANIFEST.json`` pins this file's SHA-256. The design note is ``docs/url_hostfacts.md``.

What the host computes for each URL effect of the call. The complete effect inventory, each
effect's operation and its decoding profile come from the trusted tool schema, never from policy
prose or caller claims.

1. Origin. The URL must be absolute, ``scheme://authority...`` with scheme http, https, ws or wss.
   The origin is ``scheme://host:port`` with every part explicit:
   - the scheme and the host are lowercased;
   - userinfo (text before a single ``@`` in the authority) is not part of the origin;
   - one trailing dot of the host is removed;
   - a non-ASCII host becomes IDNA A-labels (``xn--``), so a look-alike of an ASCII name is a
     different host; an A-label written by the caller is kept and must round-trip;
   - IPv6 literals take their compressed form; an IPv4 host must be a plain dotted quad;
   - a missing or empty port becomes the scheme's default (80 for http and ws, 443 for https and
     wss); an explicit port is read as a decimal number.
2. Path. The path runs from the authority to the first ``?`` or ``#`` (an empty path is ``/``) and
   is read under exactly one decoding profile, the one the effect declares:
   - ``rfc3986``: percent sequences of unreserved characters (letters, digits, ``-._~``) are
     decoded; every other sequence stays encoded, with uppercase hex; non-ASCII characters are
     written as UTF-8 percent sequences. An encoded slash ``%2F`` stays inside its segment.
   - ``decode_all_once``: every percent sequence is decoded exactly once, as UTF-8, and a decoded
     ``/`` separates segments. What is left is literal text and is never decoded again.
   Then ``.`` segments are dropped and ``..`` pops one segment, never above the root.
3. Two profile-independent observations of the path as written: whether any percent sequence
   stands for a separator (``%2F``, ``%5C``) or a segment is ``.`` or ``..`` only once decoded;
   and whether a second full decoding would change the result of the first (double encoding).
4. Comparison. A policy rule may declare URL prefixes in its optional ``urls`` field. For each
   URL effect the host lists the rules with a declared prefix of the same origin and those with
   none; and the rules with a declared prefix that contains the URL (same origin, and the decoded
   path equal to or beneath the prefix path, segment by segment, never by string prefix) and
   those with none. A rule whose ``for`` list does not cover the effect's operation appears in no
   list.

Policy authoring stays plain (see ``compile_policy``). A rule adds one optional field, for example
``"urls": {"within": "https://api.example.com/v1/", "for": ["send"]}``. ``within`` is one URL or a
list; a URL without a path declares the whole origin. The rule's description still says what the
rule means; the host never reads it. Policy mistakes are reported once, at load time.

Facts are measurements and comparison lists, never ``rule_matches``, an exception, consent,
authorization or a decision. A malformed request raises :class:`Refusal`. A call URL the host
cannot read exactly (see ``UNUSABLE_CODES``) never becomes an origin or a path: that effect gets
the single fact ``call_url_<k>_unusable`` with a stable code, and the result names it in
``unusable``, so the caller treats the required fact as unusable (a non-allow), never as absent.
"""

import ipaddress
import re

PROFILE = "armor-hostfacts-urls-v1"
PROVENANCE = "host_computed"
OPERATIONS = ("fetch", "send", "open")
DECODINGS = ("rfc3986", "decode_all_once")
DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}
MAX_EFFECTS = 8
MAX_URL_RULES = 32
MAX_PREFIXES = 8
MAX_RULES = 256
MAX_URL = 4096

REFUSAL_CODES = (
    "bad_request",  # malformed request shape, unknown keys or enum values, invalid Unicode
    "bad_url_rules",  # compiled URL rules invalid (compile_policy output was not used)
)

UNUSABLE_CODES = (
    "empty_url",
    "url_too_long",  # more than MAX_URL characters
    "whitespace_or_control",  # clients strip or keep these differently
    "backslash",  # some clients read \ as /, others as data
    "no_scheme",  # a relative reference: its origin depends on a base the host does not have
    "unsupported_scheme",  # not http, https, ws or wss
    "missing_authority_slashes",  # not exactly scheme:// followed by a host
    "empty_host",
    "ambiguous_userinfo",  # more than one @ in the authority
    "percent_encoded_host",
    "idna_deviation_character",  # IDNA 2003 and 2008 map this host differently
    "idna_failed",  # the host has no IDNA 2003 A-label form
    "bad_a_label",  # an xn-- label that does not round-trip
    "bad_host",  # empty label, bad character, label or name too long, bad IP literal
    "ipv6_zone",
    "ambiguous_ipv4_form",  # numeric host that is not a plain dotted quad (hex, octal, short)
    "bad_port",
    "bad_percent_encoding",  # a % in the path that is not followed by two hex digits
    "non_utf8_path",  # decode_all_once: the decoded bytes are not UTF-8
    "encoded_control_character",  # decode_all_once: a decoded C0, DEL or C1 control
    "encoded_backslash",  # decode_all_once: a decoded \
    "empty_path_segment",  # // inside the path: servers merge or keep empty segments
)

POLICY_ERRORS = (
    "bad_policy",  # not a list of rule objects with unique, valid ids
    "bad_urls_field",  # "urls" is not {"within": ..., "for": [...]}
    "bad_prefix",  # a declared URL the host cannot compare exactly
    "too_many_url_rules",
)

_POINTER = re.compile(r"(?:/(?:[^~/\ud800-\udfff]|~[01])*)+")
_RULE_ID = re.compile(r"[^\x00-\x1f\x7f\ud800-\udfff]{1,64}")
_INSTANT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*):")
_TRIPLET = re.compile(r"%[0-9A-Fa-f]{2}")
_TRIPLET_BYTES = re.compile(rb"%[0-9A-Fa-f]{2}")
_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_\-]{0,61}[a-z0-9_])?")
_QUAD = re.compile(r"0|[1-9][0-9]{0,2}")
_ORIGIN = re.compile(r"(?:https?|wss?)://[^/?#@\\]+:(?:0|[1-9][0-9]{0,4})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_DEVIATIONS = "ßς‌‍"


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


class Unusable(ValueError):
    """One URL the host cannot read exactly. ``compute`` turns it into a fact, never a value."""

    def __init__(self, code, detail):
        assert code in UNUSABLE_CODES, code
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _require(ok, code, detail):
    if not ok:
        raise Refusal(code, detail)


def _usable(ok, code, detail):
    if not ok:
        raise Unusable(code, detail)


def _keys(value, required, code="bad_request", where="request"):
    _require(type(value) is dict, code, f"{where} must be an object")
    _require(set(value) == set(required), code, f"{where} keys must be {sorted(required)}")


def _host_name(name, steps):
    """The comparable form of a registered name or IPv4 host, or Unusable."""
    _usable(name != "", "empty_host", "the URL has no host")
    _usable("%" not in name, "percent_encoded_host", "the host holds a percent sequence")
    converted = False
    if not name.isascii():
        _usable(
            not any(ch in _DEVIATIONS for ch in name),
            "idna_deviation_character",
            "the host holds a character that IDNA 2003 and IDNA 2008 map differently",
        )
        try:
            name = name.encode("idna").decode("ascii")
        except UnicodeError:
            raise Unusable("idna_failed", "the host has no IDNA 2003 A-label form") from None
        converted = True
        steps.append("non-ASCII host mapped by IDNA 2003 ToASCII (A-labels, xn--)")
    if name != name.lower():
        name = name.lower()
        steps.append("uppercase host letters lowercased")
    if name.endswith("."):
        name = name[:-1]
        steps.append("one trailing dot removed from the host")
    labels = name.split(".")
    _usable(
        0 < len(name) <= 253 and all(_LABEL.fullmatch(label) for label in labels),
        "bad_host",
        "the host has an empty or over-long label, or a character outside a-z 0-9 - _",
    )
    last = labels[-1]
    if last.isdigit() or re.fullmatch(r"0x[0-9a-f]*", last):
        _usable(
            len(labels) == 4 and all(_QUAD.fullmatch(x) and int(x) <= 255 for x in labels),
            "ambiguous_ipv4_form",
            "a numeric host that is not four plain decimal numbers 0..255",
        )
    for label in labels:
        if label.startswith("xn--"):
            try:
                again = label.encode("ascii").decode("idna").encode("idna").decode("ascii")
            except UnicodeError:
                again = None
            _usable(again == label, "bad_a_label", "an xn-- label that does not round-trip")
            if not converted:
                converted = True
                steps.append("host has IDNA A-labels (xn--), compared as written")
    return name


def _authority(authority, scheme, steps):
    """(host, port) of an authority, or Unusable."""
    _usable(authority != "", "empty_host", "the URL has no host")
    _usable(authority.count("@") <= 1, "ambiguous_userinfo", "more than one @ in the authority")
    if "@" in authority:
        authority = authority.split("@")[1]
        steps.append("userinfo before @ is not part of the origin and was ignored")
    if authority.startswith("["):
        end = authority.find("]")
        _usable(end != -1, "bad_host", "an IPv6 literal without a closing bracket")
        literal, rest = authority[1:end], authority[end + 1 :]
        _usable("%" not in literal, "ipv6_zone", "an IPv6 literal with a zone or percent sequence")
        try:
            compressed = ipaddress.IPv6Address(literal).compressed
        except ValueError:
            raise Unusable("bad_host", "not an IPv6 literal") from None
        if compressed != literal:
            steps.append("IPv6 literal written in its compressed lowercase form")
        _usable(rest == "" or rest.startswith(":"), "bad_host", "text after the IPv6 literal")
        host, port_text = f"[{compressed}]", rest[1:] if rest else None
    else:
        _usable(authority.count(":") <= 1, "bad_port", "more than one colon after the host")
        name, colon, port_text = authority.partition(":")
        host, port_text = _host_name(name, steps), port_text if colon else None
    default = DEFAULT_PORTS[scheme]
    if port_text is None or port_text == "":
        what = "no port written" if port_text is None else "empty port"
        steps.append(f"{what}: default port {default} for {scheme}")
        return host, default
    _usable(
        re.fullmatch(r"[0-9]{1,10}", port_text) is not None and int(port_text) <= 65535,
        "bad_port",
        "the port is not a decimal number 0..65535",
    )
    port = int(port_text)
    step = f"explicit port {port} kept"
    if str(port) != port_text:
        step += " (leading zeros dropped)"
    if port == default:
        step += f"; it is the default for {scheme}"
    steps.append(step)
    return host, port


def split(raw):
    """{"origin", "path", "query", "fragment", "userinfo", "steps"} of an absolute URL, or Unusable.

    ``path`` is the text as written (possibly empty); ``query`` and ``fragment`` say whether the
    URL has one; ``steps`` lists what normalization changed, in order.
    """
    _usable(raw != "", "empty_url", "the URL is empty")
    _usable(len(raw) <= MAX_URL, "url_too_long", f"more than {MAX_URL} characters")
    _usable(
        not any(ch.isspace() or ord(ch) < 0x21 or 0x7F <= ord(ch) <= 0x9F for ch in raw),
        "whitespace_or_control",
        "the URL holds whitespace or a control character",
    )
    _usable("\\" not in raw, "backslash", "the URL holds a backslash")
    match = _SCHEME.match(raw)
    _usable(match is not None, "no_scheme", "the URL has no scheme: it is a relative reference")
    steps = []
    scheme = match.group(1).lower()
    if scheme != match.group(1):
        steps.append("uppercase scheme letters lowercased")
    _usable(scheme in DEFAULT_PORTS, "unsupported_scheme", "the scheme is not http, https, ws, wss")
    rest = raw[match.end() :]
    _usable(
        rest.startswith("//") and not rest.startswith("///"),
        "missing_authority_slashes",
        "the scheme is not followed by exactly // and a host",
    )
    rest = rest[2:]
    cut = min((rest.find(ch) for ch in "/?#" if ch in rest), default=len(rest))
    userinfo = "@" in rest[:cut]
    host, port = _authority(rest[:cut], scheme, steps)
    tail = rest[cut:]
    end = min((tail.find(ch) for ch in "?#" if ch in tail), default=len(tail))
    after = tail[end:]
    return {
        "origin": f"{scheme}://{host}:{port}",
        "path": tail[:end],
        "query": after.startswith("?"),
        "fragment": "#" in after,
        "userinfo": userinfo,
        "steps": steps,
    }


def _decode_bytes(text):
    """Every percent sequence of ``text`` as its byte; every other character as UTF-8."""
    out, i = bytearray(), 0
    while i < len(text):
        if text[i] == "%":
            out.append(int(text[i + 1 : i + 3], 16))
            i += 3
        else:
            out += text[i].encode("utf-8")
            i += 1
    return bytes(out)


def _rfc3986(segment):
    out, i = [], 0
    while i < len(segment):
        ch = segment[i]
        if ch == "%":
            decoded = chr(int(segment[i + 1 : i + 3], 16))
            out.append(decoded if decoded in _UNRESERVED else segment[i : i + 3].upper())
            i += 3
        else:
            out.append(ch if ch.isascii() else "".join(f"%{b:02X}" for b in ch.encode("utf-8")))
            i += 1
    return "".join(out)


def read_path(raw_path, decoding):
    """The path as written, read under one decoding profile, or Unusable.

    Returns {"decoded_path", "segments", "encoded_separators", "encoded_dot_segments",
    "double_encoded", "steps"}. ``segments`` excludes the root and any trailing slash.
    """
    assert decoding in DECODINGS, decoding
    steps = []
    if raw_path == "":
        raw_path = "/"
        steps.append("empty path read as /")
    for at in (i for i, ch in enumerate(raw_path) if ch == "%"):
        _usable(
            _TRIPLET.match(raw_path, at) is not None,
            "bad_percent_encoding",
            "a % in the path is not followed by two hex digits",
        )
    written = raw_path.split("/")[1:]
    once = _decode_bytes(raw_path)
    if decoding == "rfc3986":
        segments = [_rfc3986(segment) for segment in written]
    else:
        try:
            text = once.decode("utf-8")
        except UnicodeDecodeError:
            raise Unusable("non_utf8_path", "the decoded path is not UTF-8") from None
        _usable(
            not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in text),
            "encoded_control_character",
            "a percent sequence decodes to a control character",
        )
        _usable("\\" not in text, "encoded_backslash", "a percent sequence decodes to a backslash")
        segments = text.split("/")[1:]
    _usable(
        all(segment != "" for segment in segments[:-1]),
        "empty_path_segment",
        "the path has an empty segment (//), which servers merge or keep",
    )
    trailing = segments[-1] in ("", ".", "..")
    kept, above_root = [], False
    for segment in segments:
        if segment == "..":
            above_root = above_root or not kept
            del kept[-1:]
        elif segment not in ("", "."):
            kept.append(segment)
    if above_root:
        steps.append("a .. segment at the root was dropped")
    return {
        "decoded_path": "/" + "/".join(kept) + ("/" if trailing and kept else ""),
        "segments": kept,
        "encoded_separators": len(re.findall(r"%(?:2[Ff]|5[Cc])", raw_path)),
        "encoded_dot_segments": sum(
            1 for s in written if "%" in s and _decode_bytes(s) in (b".", b"..")
        ),
        "double_encoded": _TRIPLET_BYTES.search(once) is not None,
        "steps": steps,
    }


def _policy_prefix(text, rule_id):
    def fail(message, hint):
        raise PolicyError("bad_prefix", rule_id, message, hint)

    if type(text) is not str or text == "":
        fail("a URL prefix must be a nonempty string", 'for example "https://api.example.com/v1/"')
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        fail("a URL prefix is not valid Unicode", "retype it")
    if "*" in text:
        fail(f"URL prefix {text!r} is a pattern", "name one origin, without wildcards")
    try:
        parts = split(text)
    except Unusable as exc:
        fail(f"URL prefix {text!r} cannot be compared exactly: {exc.detail}", f"code {exc.code}")
    if parts["userinfo"] or parts["query"] or parts["fragment"]:
        fail(f"URL prefix {text!r} has userinfo, a query or a fragment", "leave them out")
    if "one trailing dot removed from the host" in parts["steps"]:
        fail(f"URL prefix {text!r} has a host ending in a dot", "write the host without it")
    path = parts["path"] or "/"
    if not path.isascii() or "%" in path:
        fail(
            f"URL prefix {text!r} has a percent sequence or a non-ASCII path",
            "write the path in plain ASCII, as the decoded path facts show it",
        )
    segments = path.split("/")[1:]
    if segments[-1] == "":
        segments.pop()
    if any(segment in ("", ".", "..") for segment in segments):
        fail(f"URL prefix {text!r} has an empty, . or .. path segment", "write the final path")
    return {"origin": parts["origin"], "path": "/" + "/".join(segments)}


def compile_policy(rules):
    """URL rules for ``compute`` from AgentArmor policy rules (the parsed JSONL objects).

    Reads only each rule's ``id`` and its optional ``urls`` field; never the description.
    ``"urls": {"within": URL | [URL, ...], "for": [OPERATION, ...]}``, where URL is an absolute
    http, https, ws or wss URL without userinfo, query or fragment, whose path is plain ASCII
    with no percent sequence and no ``.``, ``..`` or empty segment, and OPERATION is one of
    fetch, send and open. A URL without a path declares its whole origin. ``for`` defaults to all
    operations. Raises :class:`PolicyError`.
    """
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
        if "urls" not in rule:
            continue
        urls = rule["urls"]
        if type(urls) is not dict or "within" not in urls or set(urls) - {"within", "for"}:
            extra = sorted(set(urls) - {"within", "for"}) if type(urls) is dict else []
            hint = f"unknown key {extra[0]!r}; " if extra else ""
            raise PolicyError(
                "bad_urls_field",
                rid,
                '"urls" must be {"within": ..., "for": [...]}',
                hint + '"within" is required and "for" is optional',
            )
        listed = urls["within"] if type(urls["within"]) is list else [urls["within"]]
        if not 1 <= len(listed) <= MAX_PREFIXES:
            raise PolicyError(
                "bad_urls_field", rid, f'"within" needs 1..{MAX_PREFIXES} URLs', "list fewer"
            )
        ops = urls.get("for", list(OPERATIONS))
        if (
            type(ops) is not list
            or not ops
            or len(set(map(str, ops))) != len(ops)
            or any(o not in OPERATIONS for o in ops)
        ):
            raise PolicyError(
                "bad_urls_field",
                rid,
                '"for" must list distinct operations',
                "use fetch, send or open, or leave it out for all",
            )
        prefixes = [_policy_prefix(text, rid) for text in listed]
        if len({(p["origin"], p["path"]) for p in prefixes}) != len(prefixes):
            raise PolicyError("bad_urls_field", rid, "a URL is listed twice", "list it once")
        out.append({"rule_id": rid, "prefixes": prefixes, "operations": list(ops)})
    if len(out) > MAX_URL_RULES:
        raise PolicyError(
            "too_many_url_rules",
            None,
            f"{len(out)} rules declare urls; at most {MAX_URL_RULES} are supported",
            "merge rules that share an origin",
        )
    return out


def _effects(value):
    _require(
        type(value) is list and len(value) <= MAX_EFFECTS,
        "bad_request",
        f"effects must list 0..{MAX_EFFECTS} URL effects: the tool schema's complete inventory",
    )
    seen = set()
    for k, effect in enumerate(value, start=1):
        _keys(effect, ("argument", "raw", "operation", "decoding"), where=f"effects[{k}]")
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
        _require(
            type(effect["raw"]) is str
            and not any(0xD800 <= ord(ch) <= 0xDFFF for ch in effect["raw"]),
            "bad_request",
            f"effects[{k}].raw must be a string of valid Unicode",
        )
        _require(effect["operation"] in OPERATIONS, "bad_request", f"effects[{k}].operation")
        _require(effect["decoding"] in DECODINGS, "bad_request", f"effects[{k}].decoding")
    return value


def _url_rules(value):
    _require(
        type(value) is list and len(value) <= MAX_URL_RULES,
        "bad_url_rules",
        f"url_rules must list at most {MAX_URL_RULES} rules (compile_policy output)",
    )
    ids = set()
    for j, r in enumerate(value, start=1):
        where = f"url_rules[{j}]"
        _keys(r, ("rule_id", "prefixes", "operations"), code="bad_url_rules", where=where)
        _require(
            type(r["rule_id"]) is str
            and _RULE_ID.fullmatch(r["rule_id"]) is not None
            and r["rule_id"] not in ids,
            "bad_url_rules",
            f"{where}.rule_id must be unique, 1..64 characters without controls",
        )
        ids.add(r["rule_id"])
        prefixes = r["prefixes"]
        _require(
            type(prefixes) is list and 1 <= len(prefixes) <= MAX_PREFIXES,
            "bad_url_rules",
            f"{where}.prefixes must list 1..{MAX_PREFIXES} prefixes",
        )
        for p in prefixes:
            _keys(p, ("origin", "path"), code="bad_url_rules", where=f"{where}.prefixes")
            origin, path = p["origin"], p["path"]
            ok = type(origin) is str and type(path) is str and len(origin) + len(path) <= MAX_URL
            ok = ok and _ORIGIN.fullmatch(origin) is not None and path.startswith("/")
            if ok:
                try:
                    ok = _policy_prefix(origin + path, r["rule_id"]) == p
                except PolicyError:
                    ok = False
            _require(ok, "bad_url_rules", f"{where}: a prefix is not in compiled form")
        ops = r["operations"]
        _require(
            type(ops) is list
            and ops
            and len(set(map(str, ops))) == len(ops)
            and all(o in OPERATIONS for o in ops),
            "bad_url_rules",
            f"{where}.operations must be a nonempty list of distinct operations",
        )
    return value


_DECODING_NOTE = {
    "rfc3986": (
        "decoding profile rfc3986: percent sequences of unreserved characters (letters, digits, "
        "- . _ ~) decoded, every other percent sequence kept with uppercase hex, non-ASCII "
        "characters written as UTF-8 percent sequences, so an encoded / (%2F) stays inside its "
        "segment"
    ),
    "decode_all_once": (
        "decoding profile decode_all_once: every percent sequence decoded exactly once as UTF-8, "
        "a decoded / separates segments, and any percent sequence left is literal text that is "
        "not decoded again"
    ),
}
_TAIL = "Comparison only: not a rule match, exception or decision."


def _applied(steps):
    return "Applied here: " + ("; ".join(steps) if steps else "nothing changed") + "."


def compute(request):
    """The facts for one call in a fixed order, or a Refusal.

    Returns {"profile", "status": "ok", "facts", "unusable"}; ``unusable`` lists the effect numbers
    whose URL the host could not read (each has only ``call_url_<k>_unusable``). Per readable URL
    effect ``k`` in inventory order: ``call_url_<k>_origin``; when the policy has URL rules,
    ``call_url_<k>_same_origin_as`` and ``call_url_<k>_different_origin_from``; then
    ``call_url_<k>_decoded_path``, ``call_url_<k>_encoded_separator_or_dot_segment`` and
    ``call_url_<k>_double_encoded``; and, when the policy has URL rules,
    ``call_url_<k>_inside_prefixes_of`` and ``call_url_<k>_outside_prefixes_of``. Lists hold rule
    ids in policy order. ``url_rules`` is ``compile_policy`` output.
    """
    _keys(request, ("profile", "observed_at", "effects", "url_rules"))
    _require(request["profile"] == PROFILE, "bad_request", f"profile must be {PROFILE}")
    observed_at = request["observed_at"]
    _require(
        type(observed_at) is str and _INSTANT.fullmatch(observed_at) is not None,
        "bad_request",
        "observed_at must be an ISO timestamp with seconds and an explicit offset",
    )
    effects = _effects(request["effects"])
    rules = _url_rules(request["url_rules"])

    def fact(name, value, note):
        return {
            "name": name,
            "value": value,
            "unit": None,
            "provenance": PROVENANCE,
            "as_of": observed_at,
            "note": note,
        }

    facts, unusable = [], []
    for k, e in enumerate(effects, start=1):
        what = f"call argument {e['argument']} for {e['operation']}"
        try:
            parts = split(e["raw"])
            path = read_path(parts["path"], e["decoding"])
        except Unusable as exc:
            unusable.append(k)
            facts.append(
                fact(
                    f"call_url_{k}_unusable",
                    exc.code,
                    f"The host could not read the URL in {what}: {exc.detail}. No origin, decoded "
                    "path or comparison fact exists for this argument, and none may be inferred "
                    "from its raw text.",
                )
            )
            continue
        selected = [r for r in rules if e["operation"] in r["operations"]]
        covers = f"Rules whose urls field does not cover {e['operation']} are in neither list."
        facts.append(
            fact(
                f"call_url_{k}_origin",
                parts["origin"],
                f"Host reading of {what} as scheme://host:port, without network access: scheme "
                "and host lowercased, userinfo ignored, one trailing host dot removed, non-ASCII "
                "host as IDNA A-labels, the scheme's default port when none is written. "
                + _applied(parts["steps"]),
            )
        )
        if rules:
            same = [
                r["rule_id"]
                for r in selected
                if any(p["origin"] == parts["origin"] for p in r["prefixes"])
            ]
            facts.append(
                fact(
                    f"call_url_{k}_same_origin_as",
                    same,
                    f"Policy rules with a declared URL whose origin equals call_url_{k}_origin "
                    f"(scheme, host and port all equal). {covers} {_TAIL}",
                )
            )
            facts.append(
                fact(
                    f"call_url_{k}_different_origin_from",
                    [r["rule_id"] for r in selected if r["rule_id"] not in same],
                    "Policy rules none of whose declared URLs has the origin "
                    f"call_url_{k}_origin. {covers} {_TAIL}",
                )
            )
        facts.append(
            fact(
                f"call_url_{k}_decoded_path",
                path["decoded_path"],
                f"Path of {what} under {_DECODING_NOTE[e['decoding']]}; then . segments dropped "
                "and .. popping one segment, never above the root. Query and fragment excluded. "
                + _applied(path["steps"]),
            )
        )
        facts.append(
            fact(
                f"call_url_{k}_encoded_separator_or_dot_segment",
                path["encoded_separators"] + path["encoded_dot_segments"] > 0,
                "Whether the path as written holds a percent sequence for a separator (%2F or "
                "%5C, any hex case) or a segment that is . or .. only after decoding. Found: "
                f"{path['encoded_separators']} encoded separator(s), "
                f"{path['encoded_dot_segments']} encoded dot segment(s).",
            )
        )
        facts.append(
            fact(
                f"call_url_{k}_double_encoded",
                path["double_encoded"],
                "Whether decoding every percent sequence of the path once leaves a percent "
                "sequence, so that a second decoding would change it again. "
                f"call_url_{k}_decoded_path is never decoded a second time.",
            )
        )
        if not rules:
            continue
        inside = [
            r["rule_id"]
            for r in selected
            if any(
                p["origin"] == parts["origin"] and path["segments"][: len(q)] == q
                for p in r["prefixes"]
                for q in [[s for s in p["path"].split("/") if s]]
            )
        ]
        facts.append(
            fact(
                f"call_url_{k}_inside_prefixes_of",
                inside,
                f"Policy rules with a declared URL prefix of origin call_url_{k}_origin whose "
                f"path contains call_url_{k}_decoded_path (equal or beneath, segment by segment, "
                f"case-sensitive, never by string prefix). {covers} {_TAIL}",
            )
        )
        facts.append(
            fact(
                f"call_url_{k}_outside_prefixes_of",
                [r["rule_id"] for r in selected if r["rule_id"] not in inside],
                "Policy rules none of whose declared URL prefixes contains this URL: another "
                f"origin, or a path that does not contain call_url_{k}_decoded_path. "
                f"{covers} {_TAIL}",
            )
        )
    return {"profile": PROFILE, "status": "ok", "facts": facts, "unusable": unusable}


def compute_or_refusal(request):
    """``compute`` with refusals as data: {"profile", "status": "refused", "code", "detail"}."""
    try:
        return compute(request)
    except Refusal as exc:
        return {"profile": PROFILE, "status": "refused", "code": exc.code, "detail": exc.detail}
