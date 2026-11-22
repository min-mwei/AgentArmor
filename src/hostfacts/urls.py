"""Host-computed URL facts, profile armor-hostfacts-urls-v1.

CPU reference. A candidate profile, not a registered serving identity until the trainer registers
it. AgentArmor computes these facts deterministically and passes them to Kai as trusted
``host_computed`` environment facts; Kai is trained to defer to them. Standard library only
(``ipaddress``, ``re``, ``unicodedata`` and the ``idna`` codec). No network: no DNS lookup, no
redirect, no fetch.

Like ``paths.py`` and ``measures.py``, this file is meant to be kept byte-identical in Kai
(``train/kai/hostfacts/urls.py``) and AgentArmor (``src/hostfacts/urls.py``). Both copies must give
the hand-written results in the shared ``armor-hostfacts-urls-v1/vectors.json``, whose
``MANIFEST.json`` pins this file's SHA-256. The design note is ``docs/url_hostfacts.md``.

The rule of this file: a fact never guesses. Kai has no knowledge of its own about URLs, so
whenever a mainstream client or server could reach another resource than a fact would claim, the
host refuses the request (:class:`Refusal`, a stable code) instead of stating the fact.

What the host computes for each URL effect of the call. The complete effect inventory and, for
each effect, its operation, its decoding profile, whether the server compares path names
case-sensitively and whether it keeps ``;`` as path data come from the trusted tool profile,
never from policy prose or caller claims.

1. Origin. The URL must be absolute, ``scheme://authority...`` with scheme http, https, ws or wss.
   The origin is ``scheme://host:port`` with every part explicit, and origins are compared
   exactly (no subdomain or wildcard relation):
   - the scheme and the host are lowercased;
   - userinfo (text before a single ``@`` in the authority) is not part of the origin;
   - a non-ASCII host becomes IDNA A-labels (``xn--``), so a look-alike of an ASCII name is a
     different host; an A-label written by the caller is kept and must round-trip;
   - IPv6 literals take their compressed form; an IPv4 host must be a plain dotted quad;
   - a missing or empty port becomes the scheme's default (80 for http and ws, 443 for https and
     wss); an explicit port is read as a decimal number 1..65535.
   Refused, among others: a host ending in a dot; a host character that Unicode 3.2 (the IDNA
   2003 table) does not assign, or that IDNA 2003 and 2008 map differently; numeric hosts that are
   not a plain dotted quad; IPv6 literals that carry an IPv4 address (mapped, compatible,
   translated, NAT64, 6to4, Teredo); the unspecified addresses 0.0.0.0 and [::]; whitespace,
   controls and backslashes anywhere.
2. Path. The path runs from the authority to the first ``?`` or ``#`` (an empty path is ``/``) and
   is read under exactly one decoding profile, the one the effect declares:
   - ``rfc3986``: percent sequences of unreserved characters (letters, digits, ``-._~``) are
     decoded; every other sequence stays encoded, with uppercase hex; non-ASCII characters and
     ASCII characters that RFC 3986 does not allow in a path are written as UTF-8 percent
     sequences. An encoded slash ``%2F`` stays inside its segment.
   - ``decode_all_once``: every percent sequence is decoded exactly once, as UTF-8, and a decoded
     ``/`` separates segments. What is left is literal text and is never decoded again.
   Then ``.`` segments are dropped and ``..`` pops one segment, never above the root.
   Refused under both profiles: a ``%`` without two hex digits; decoded bytes that are not UTF-8
   or hold a control character; an empty segment; a ``;`` in a segment unless the tool profile
   declares it path data; and, for a server declared case-insensitive, non-ASCII path text,
   segments that end in a dot or a space or consist of three or more dots, and segments with a
   ``:`` or a ``~`` before a digit (streams and short names of the Windows family). Refused
   under ``decode_all_once`` only, where decoded text is shown: a decoded backslash and decoded
   invisible, separator or private-use characters and characters Unicode 3.2 does not assign
   (a fixed table, the same on every Python).
3. Two profile-independent observations of the path as written: whether any percent sequence
   stands for a separator (``%2F``, ``%5C``) or a segment is ``.`` or ``..`` only once decoded;
   and whether a second full decoding would change the result of the first (double encoding).
   When it would, the host also reads the path decoded again, and again until no percent
   sequence is left, and refuses if any of those readings is itself refused, yields a separator
   or a dot segment the first decoding did not, or would change any comparison list.
4. Comparison. A policy rule may declare URL prefixes in its optional ``urls`` field. For each
   URL effect the host lists the rules with a declared prefix of the same origin and those with
   none; and the rules with a declared prefix that contains the URL (same origin, and the decoded
   path equal to or beneath the prefix path, segment by segment, never by string prefix, with the
   case sensitivity the tool profile declares) and those with none. A rule whose ``for`` list
   does not cover the effect's operation appears in no list.

Policy authoring stays plain (see ``compile_policy``). A rule adds one optional field, for example
``"urls": {"within": "https://api.example.com/v1/", "for": ["send"]}``. ``within`` is one URL or a
list; a URL without a path declares the whole origin. The rule's description still says what the
rule means; the host never reads it. Policy mistakes are reported once, at load time.

Facts are measurements and comparison lists, never ``rule_matches``, an exception, consent,
authorization or a decision. Unknown, unsupported or ambiguous call inputs raise :class:`Refusal`
with a stable code, for the first such effect in inventory order and whether or not a rule
covers it; they never become a value.
"""

import ipaddress
import re
import unicodedata

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
    "idna_unassigned_character",  # unassigned in Unicode 3.2: only UTS 46 clients map it
    "idna_deviation_character",  # IDNA 2003 and 2008 map this host differently
    "idna_failed",  # the host has no IDNA 2003 A-label form
    "idna_mapped_separator",  # IDNA 2003 maps a character to a label dot; UTS 46 clients reject it
    "bad_a_label",  # an xn-- label that does not round-trip
    "trailing_dot_host",  # clients send example.com. as another Host and SNI than example.com
    "bad_host",  # empty label, bad character, label or name too long, bad IP literal
    "ipv6_zone",
    "ipv6_embedded_ipv4",  # mapped, compatible, translated, 6to4, Teredo: reaches an IPv4 address
    "unspecified_address",  # 0.0.0.0 and [::]: most stacks connect them to the local host
    "ambiguous_ipv4_form",  # numeric host that is not a plain dotted quad (hex, octal, short)
    "bad_port",  # not a decimal number 1..65535
    "bad_percent_encoding",  # a % in the path that is not followed by two hex digits
    "non_utf8_path",  # the decoded bytes are not UTF-8 (overlong forms included)
    "encoded_control_character",  # a decoded C0, DEL or C1 control
    "encoded_backslash",  # decode_all_once: a decoded \
    "invisible_character",  # decode_all_once: a decoded separator, format or unassigned character
    "empty_path_segment",  # // inside the path: servers merge or keep empty segments
    "path_parameter",  # a ; in a segment, which some servers strip, not declared as data
    "non_ascii_case_insensitive",  # non-ASCII path text on a case-insensitive server
    "trailing_dot_or_space_segment",  # case-insensitive servers of the Windows family drop these
    "windows_path_form",  # case-insensitive server: a short name (~1) or a stream (:) in a segment
    "ambiguous_double_decoding",  # a further decoding is unreadable or reads another path
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
_TRIPLET_BYTES = re.compile(rb"%([0-9A-Fa-f]{2})")
_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_\-]{0,61}[a-z0-9_])?")
_QUAD = re.compile(r"0|[1-9][0-9]{0,2}")
_ORIGIN = re.compile(r"(?:https?|wss?)://[^/?#@\\]+:[1-9][0-9]{0,4}")
_PREFIX_SEGMENT = re.compile(r"[A-Za-z0-9\-._~]+")
_ALPHANUMERIC = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_UNRESERVED = frozenset(_ALPHANUMERIC + "-._~")
_PATH_LITERAL = _UNRESERVED | frozenset("!$&'()*+,;=:@")  # RFC 3986 pchar without percent forms
_DEVIATIONS = "ßς‌‍"
# White_Space outside ASCII, written out so that the result does not depend on the running
# Python's Unicode tables. U+180E was a space separator until Unicode 6.3.
_SPACES = frozenset("\u0085  ᠎               　")
# Categories of the Unicode 3.2 table (the one IDNA 2003 uses), so that every Python gives the
# same answer. A character assigned after 3.2 is "Cn" there and is refused. U+00AD is the one
# character that Unicode 15.1 calls a format character and 3.2 does not.
_HIDDEN = ("Zs", "Zl", "Zp", "Cf", "Co", "Cs", "Cn")
_HIDDEN_LATER = "\u00ad"
_DOTS = ".\u3002\uff0e\uff61"  # the label separators of IDNA 2003
# IPv6 blocks whose addresses carry an IPv4 address that a translator or tunnel delivers to:
# IPv4-compatible, IPv4-mapped, IPv4-translated (SIIT), NAT64 (RFC 6052, RFC 8215), Teredo, 6to4.
_IPV4_CARRIERS = tuple(
    ipaddress.IPv6Network(block)
    for block in (
        "::/96",
        "::ffff:0:0/96",
        "::ffff:0:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/32",
        "2002::/16",
    )
)
_MAX_DECODINGS = 8


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


def _keys(value, required, code="bad_request", where="request"):
    _require(type(value) is dict, code, f"{where} must be an object")
    _require(set(value) == set(required), code, f"{where} keys must be {sorted(required)}")


def _host_name(name, steps):
    """The comparable form of a registered name or IPv4 host, or a Refusal."""
    _require(name != "", "empty_host", "the URL has no host")
    _require("%" not in name, "percent_encoded_host", "the host holds a percent sequence")
    converted = False
    if not name.isascii():
        _require(
            not any(unicodedata.ucd_3_2_0.category(ch) == "Cn" for ch in name),
            "idna_unassigned_character",
            "the host holds a character that Unicode 3.2 does not assign, which IDNA 2003 "
            "keeps and newer clients map",
        )
        _require(
            not any(ch in _DEVIATIONS for ch in name),
            "idna_deviation_character",
            "the host holds a character that IDNA 2003 and IDNA 2008 map differently",
        )
        separators = sum(ch in _DOTS for ch in name)
        try:
            name = name.encode("idna").decode("ascii")
        except UnicodeError:
            raise Refusal("idna_failed", "the host has no IDNA 2003 A-label form") from None
        _require(
            name.count(".") == separators,
            "idna_mapped_separator",
            "IDNA 2003 maps a host character to a label separator, which other clients reject",
        )
        converted = True
        steps.append("non-ASCII host mapped by IDNA 2003 ToASCII (A-labels, xn--)")
    if name != name.lower():
        name = name.lower()
        steps.append("uppercase host letters lowercased")
    _require(not name.endswith("."), "trailing_dot_host", "the host ends in a dot")
    labels = name.split(".")
    _require(
        0 < len(name) <= 253 and all(_LABEL.fullmatch(label) for label in labels),
        "bad_host",
        "the host has an empty or over-long label, or a character outside a-z 0-9 - _",
    )
    last = labels[-1]
    if last.isdigit() or re.fullmatch(r"0x[0-9a-f]*", last):
        _require(
            len(labels) == 4 and all(_QUAD.fullmatch(x) and int(x) <= 255 for x in labels),
            "ambiguous_ipv4_form",
            "a numeric host that is not four plain decimal numbers 0..255",
        )
        _require(name != "0.0.0.0", "unspecified_address", "0.0.0.0 names no single host")
    for label in labels:
        if label.startswith("xn--"):
            try:
                again = label.encode("ascii").decode("idna").encode("idna").decode("ascii")
            except UnicodeError:
                again = None
            _require(again == label, "bad_a_label", "an xn-- label that does not round-trip")
            if not converted:
                converted = True
                steps.append("host has IDNA A-labels (xn--), compared as written")
    return name


def _authority(authority, scheme, steps):
    """(host, port) of an authority, or a Refusal."""
    _require(authority != "", "empty_host", "the URL has no host")
    _require(authority.count("@") <= 1, "ambiguous_userinfo", "more than one @ in the authority")
    if "@" in authority:
        authority = authority.split("@")[1]
        steps.append("userinfo before @ is not part of the origin and was ignored")
    if authority.startswith("["):
        end = authority.find("]")
        _require(end != -1, "bad_host", "an IPv6 literal without a closing bracket")
        literal, rest = authority[1:end], authority[end + 1 :]
        _require("%" not in literal, "ipv6_zone", "an IPv6 literal with a zone or percent sequence")
        try:
            address = ipaddress.IPv6Address(literal)
        except ValueError:
            raise Refusal("bad_host", "not an IPv6 literal") from None
        _require(int(address) != 0, "unspecified_address", "[::] names no single host")
        _require(
            int(address) == 1 or not any(address in block for block in _IPV4_CARRIERS),
            "ipv6_embedded_ipv4",
            "an IPv6 literal that carries an IPv4 address (mapped, compatible, translated, "
            "NAT64, 6to4 or Teredo) reaches that IPv4 address",
        )
        if address.compressed != literal:
            steps.append("IPv6 literal written in its compressed lowercase form")
        _require(rest == "" or rest.startswith(":"), "bad_host", "text after the IPv6 literal")
        host, port_text = f"[{address.compressed}]", rest[1:] if rest else None
    else:
        _require(authority.count(":") <= 1, "bad_port", "more than one colon after the host")
        name, colon, port_text = authority.partition(":")
        host, port_text = _host_name(name, steps), port_text if colon else None
    default = DEFAULT_PORTS[scheme]
    if port_text is None or port_text == "":
        what = "no port written" if port_text is None else "empty port"
        steps.append(f"{what}: default port {default} for {scheme}")
        return host, default
    _require(
        re.fullmatch(r"[0-9]{1,10}", port_text) is not None and 1 <= int(port_text) <= 65535,
        "bad_port",
        "the port is not a decimal number 1..65535",
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
    """{"origin", "path", "query", "fragment", "userinfo", "steps"} of a URL, or a Refusal.

    ``path`` is the text as written (possibly empty); ``query`` and ``fragment`` say whether the
    URL has one; ``steps`` lists what normalization changed, in order.
    """
    _require(raw != "", "empty_url", "the URL is empty")
    _require(len(raw) <= MAX_URL, "url_too_long", f"more than {MAX_URL} characters")
    _require(
        not any(ord(ch) < 0x21 or 0x7F <= ord(ch) <= 0x9F or ch in _SPACES for ch in raw),
        "whitespace_or_control",
        "the URL holds whitespace or a control character",
    )
    _require("\\" not in raw, "backslash", "the URL holds a backslash")
    match = _SCHEME.match(raw)
    _require(match is not None, "no_scheme", "the URL has no scheme: it is a relative reference")
    steps = []
    scheme = match.group(1).lower()
    if scheme != match.group(1):
        steps.append("uppercase scheme letters lowercased")
    _require(
        scheme in DEFAULT_PORTS, "unsupported_scheme", "the scheme is not http, https, ws, wss"
    )
    rest = raw[match.end() :]
    _require(
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
            out.append(
                ch if ch in _PATH_LITERAL else "".join(f"%{b:02X}" for b in ch.encode("utf-8"))
            )
            i += 1
    return "".join(out)


def _decoded_text(data, *, shown, case_sensitive, semicolon_is_data):
    """The fully decoded path text, checked for what a server could read otherwise, or a Refusal.

    ``shown`` is true when this text is split on its decoded separators and shown in a fact.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal("non_utf8_path", "the decoded path is not UTF-8") from None
    _require(
        not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in text),
        "encoded_control_character",
        "a percent sequence decodes to a control character",
    )
    if shown:
        _require("\\" not in text, "encoded_backslash", "a percent sequence decodes to a backslash")
        _require(
            not any(
                not ch.isascii()
                and (
                    ch in _SPACES
                    or ch in _HIDDEN_LATER
                    or unicodedata.ucd_3_2_0.category(ch) in _HIDDEN
                )
                for ch in text
            ),
            "invisible_character",
            "the decoded path holds a separator, format, private-use or unassigned character",
        )
    _require(
        semicolon_is_data or ";" not in text,
        "path_parameter",
        "a path segment holds a ; and the tool profile does not declare it path data",
    )
    if not case_sensitive:
        _require(
            text.isascii(),
            "non_ascii_case_insensitive",
            "non-ASCII path text on a server declared case-insensitive is not supported",
        )
        _require(
            not any(
                segment not in ("", ".", "..") and (segment[-1] in ". " or segment.strip(".") == "")
                for segment in text.split("/")
            ),
            "trailing_dot_or_space_segment",
            "a path segment ends in a dot or a space, or is three or more dots, on a server "
            "declared case-insensitive",
        )
        _require(
            ":" not in text and re.search(r"~[0-9]", text) is None,
            "windows_path_form",
            "a path segment holds a : or a ~digit (a stream or a short name) on a server "
            "declared case-insensitive",
        )
    return text


def _resolve(segments):
    """(kept segments, trailing slash, whether a .. met the root) after dot-segment removal."""
    _require(
        all(segment != "" for segment in segments[:-1]),
        "empty_path_segment",
        "the path has an empty segment (//), which servers merge or keep",
    )
    kept, above_root = [], False
    for segment in segments:
        if segment == "..":
            above_root = above_root or not kept
            del kept[-1:]
        elif segment not in ("", "."):
            kept.append(segment)
    return kept, segments[-1] in ("", ".", ".."), above_root


def read_path(raw_path, decoding, *, case_sensitive=True, semicolon_is_data=False):
    """The path as written, read under one decoding profile, or a Refusal.

    Returns {"decoded_path", "segments", "deeper_segments", "encoded_separators",
    "encoded_dot_segments", "double_encoded", "steps"}. ``segments`` excludes the root and any
    trailing slash. ``deeper_segments`` lists the same for the path decoded a second time, a
    third time and so on until no percent sequence is left; it is empty when the path is not
    double encoded. A further decoding that cannot be read, that yields a separator or a dot
    segment the first decoding did not, or that is still encoded after ``_MAX_DECODINGS``
    passes, refuses.
    """
    assert decoding in DECODINGS, decoding
    declared = {"case_sensitive": case_sensitive, "semicolon_is_data": semicolon_is_data}
    steps = []
    if raw_path == "":
        raw_path = "/"
        steps.append("empty path read as /")
    for at in (i for i, ch in enumerate(raw_path) if ch == "%"):
        _require(
            _TRIPLET.match(raw_path, at) is not None,
            "bad_percent_encoding",
            "a % in the path is not followed by two hex digits",
        )
    written = raw_path.split("/")[1:]
    once = _decode_bytes(raw_path)
    text = _decoded_text(once, shown=decoding == "decode_all_once", **declared)
    if decoding == "rfc3986":
        segments = [_rfc3986(segment) for segment in written]
    else:
        segments = text.split("/")[1:]
    kept, trailing, above_root = _resolve(segments)
    if above_root:
        steps.append("a .. segment at the root was dropped")
    double = _TRIPLET_BYTES.search(once) is not None

    def shape(decoded):
        return [segment if segment in (".", "..") else "" for segment in decoded.split("/")]

    deeper, data = [], once
    while _TRIPLET_BYTES.search(data) is not None:
        _require(
            len(deeper) < _MAX_DECODINGS,
            "ambiguous_double_decoding",
            f"the path is still encoded after {_MAX_DECODINGS + 1} decodings",
        )
        data = _TRIPLET_BYTES.sub(lambda m: bytes([int(m.group(1), 16)]), data)
        try:
            further = _decoded_text(data, shown=True, **declared)
            deeper.append(_resolve(further.split("/")[1:])[0])
        except Refusal as exc:
            raise Refusal(
                "ambiguous_double_decoding",
                f"the path decoded {len(deeper) + 2} times cannot be read ({exc.code})",
            ) from None
        _require(
            shape(further) == shape(text),
            "ambiguous_double_decoding",
            "a further decoding of the path yields a separator or a dot segment",
        )
    return {
        "decoded_path": "/" + "/".join(kept) + ("/" if trailing and kept else ""),
        "segments": kept,
        "deeper_segments": deeper,
        "encoded_separators": len(re.findall(r"%(?:2[Ff]|5[Cc])", raw_path)),
        "encoded_dot_segments": sum(
            1 for s in written if "%" in s and _decode_bytes(s) in (b".", b"..")
        ),
        "double_encoded": double,
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
    except Refusal as exc:
        fail(f"URL prefix {text!r} cannot be compared exactly: {exc.detail}", f"code {exc.code}")
    if parts["userinfo"] or parts["query"] or parts["fragment"]:
        fail(f"URL prefix {text!r} has userinfo, a query or a fragment", "leave them out")
    segments = (parts["path"] or "/").split("/")[1:]
    if segments[-1] == "":
        segments.pop()
    if any(segment in ("", ".", "..") for segment in segments):
        fail(f"URL prefix {text!r} has an empty, . or .. path segment", "write the final path")
    if not all(_PREFIX_SEGMENT.fullmatch(segment) for segment in segments):
        fail(
            f"URL prefix {text!r} has a path character other than letters, digits and - . _ ~",
            "declare a parent path that needs none; percent sequences are not accepted either",
        )
    return {"origin": parts["origin"], "path": "/" + "/".join(segments)}


def compile_policy(rules):
    """URL rules for ``compute`` from AgentArmor policy rules (the parsed JSONL objects).

    Reads only each rule's ``id`` and its optional ``urls`` field; never the description.
    ``"urls": {"within": URL | [URL, ...], "for": [OPERATION, ...]}``, where URL is an absolute
    http, https, ws or wss URL without userinfo, query or fragment, whose path segments hold
    only letters, digits and ``-._~`` (no percent sequence, no ``.``, ``..`` or empty segment),
    and OPERATION is one of fetch, send and open. A URL without a path declares its whole origin.
    ``for`` defaults to all operations. Raises :class:`PolicyError`.
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


EFFECT_KEYS = ("argument", "raw", "operation", "decoding", "case_sensitive", "semicolon_is_data")


def _effects(value):
    _require(
        type(value) is list and len(value) <= MAX_EFFECTS,
        "bad_request",
        f"effects must list 0..{MAX_EFFECTS} URL effects: the tool schema's complete inventory",
    )
    seen = set()
    for k, effect in enumerate(value, start=1):
        _keys(effect, EFFECT_KEYS, where=f"effects[{k}]")
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
        for name in ("case_sensitive", "semicolon_is_data"):
            _require(
                type(effect[name]) is bool, "bad_request", f"effects[{k}].{name} must be a boolean"
            )
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
        "characters and ASCII characters not allowed in a path written as UTF-8 percent "
        "sequences, so an encoded / (%2F) stays inside its segment"
    ),
    "decode_all_once": (
        "decoding profile decode_all_once: every percent sequence decoded exactly once as UTF-8, "
        "a decoded / separates segments, and any percent sequence left is literal text that is "
        "not decoded again, so any ? or # shown is decoded data"
    ),
}
_TAIL = "Comparison only: not a rule match, exception or decision."


def _applied(steps):
    return "Applied here: " + ("; ".join(steps) if steps else "nothing changed") + "."


def compute(request):
    """The facts for one call in a fixed order, {"profile", "status": "ok", "facts"}, or a Refusal.

    Per URL effect ``k`` in inventory order: ``call_url_<k>_origin``; when the policy has URL
    rules, ``call_url_<k>_same_origin_as`` and ``call_url_<k>_different_origin_from``; then
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

    facts = []
    for k, e in enumerate(effects, start=1):
        what = f"call argument {e['argument']} for {e['operation']}"
        parts = split(e["raw"])
        path = read_path(
            parts["path"],
            e["decoding"],
            case_sensitive=e["case_sensitive"],
            semicolon_is_data=e["semicolon_is_data"],
        )
        selected = [r for r in rules if e["operation"] in r["operations"]]
        fold = (lambda s: s) if e["case_sensitive"] else str.lower

        def containing(segments, parts=parts, selected=selected, fold=fold):
            read = [fold(s) for s in segments]
            return [
                r["rule_id"]
                for r in selected
                if any(
                    p["origin"] == parts["origin"] and read[: len(q)] == q
                    for p in r["prefixes"]
                    for q in [[fold(s) for s in p["path"].split("/") if s]]
                )
            ]

        inside = containing(path["segments"])
        _require(
            all(containing(segments) == inside for segments in path["deeper_segments"]),
            "ambiguous_double_decoding",
            "decoding the path again would change which declared prefixes contain it",
        )
        covers = f"Rules whose urls field does not cover {e['operation']} are in neither list."
        facts.append(
            fact(
                f"call_url_{k}_origin",
                parts["origin"],
                f"Host reading of {what} as scheme://host:port, without network access: scheme "
                "and host lowercased, userinfo ignored, non-ASCII host as IDNA A-labels, the "
                "scheme's default port when none is written. " + _applied(parts["steps"]),
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
                + (
                    "A ; is path data, as the tool profile declares. "
                    if e["semicolon_is_data"]
                    else ""
                )
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
                f"call_url_{k}_decoded_path is never decoded a second time; the host refuses a "
                "URL whose comparison lists a second decoding would change.",
            )
        )
        if not rules:
            continue
        case = "case-sensitive" if e["case_sensitive"] else "ASCII case-insensitive"
        facts.append(
            fact(
                f"call_url_{k}_inside_prefixes_of",
                inside,
                f"Policy rules with a declared URL prefix of origin call_url_{k}_origin whose "
                f"path contains call_url_{k}_decoded_path (equal or beneath, segment by segment, "
                f"{case} as the tool profile declares, never by string prefix). "
                f"{covers} {_TAIL}",
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
    return {"profile": PROFILE, "status": "ok", "facts": facts}


def compute_or_refusal(request):
    """``compute`` with refusals as data: {"profile", "status": "refused", "code", "detail"}."""
    try:
        return compute(request)
    except Refusal as exc:
        return {"profile": PROFILE, "status": "refused", "code": exc.code, "detail": exc.detail}
