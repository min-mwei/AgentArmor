"""Writes vectors.json and MANIFEST.json for armor-hostfacts-urls-v1 from hand-written cases.

The expected values below were written by hand from RFC 3986 (syntax, percent-encoding, dot
segments), RFC 6454 (origin) and the profile's stated rules. This builder never imports or calls
the reference implementation, so the vectors are an independent check of the Python reference and
of any port. The two A-label strings are the Punycode of their hosts (RFC 3492). The cases of
the 2026-11-22 independent review (ids rv_*) carry that review's probe policy and inputs.
Usage: python3 -I make_vectors.py [PATH_TO_urls.py]
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = "armor-hostfacts-urls-v1"
AT = "2026-11-22T12:00:00Z"
ALL = ["fetch", "send", "open"]
EX = "https://example.com:443"

CASES, POLICY_CASES = [], []


def eff(raw, op="fetch", decoding="rfc3986", arg="/call/arguments/url", cs=True, semi=False):
    return {
        "argument": arg,
        "raw": raw,
        "operation": op,
        "decoding": decoding,
        "case_sensitive": cs,
        "semicolon_is_data": semi,
    }


def rule(rid, *prefixes, ops=ALL):
    return {
        "rule_id": rid,
        "prefixes": [{"origin": o, "path": p} for o, p in prefixes],
        "operations": list(ops),
    }


def ok(
    origin, path, flag=False, double=False, same=None, diff=None, inside=None, outside=None, k=1
):
    """The facts of URL effect k; the four lists only when the policy has URL rules."""
    out = [(f"call_url_{k}_origin", origin)]
    if same is not None:
        out += [
            (f"call_url_{k}_same_origin_as", same),
            (f"call_url_{k}_different_origin_from", diff),
        ]
    out += [
        (f"call_url_{k}_decoded_path", path),
        (f"call_url_{k}_encoded_separator_or_dot_segment", flag),
        (f"call_url_{k}_double_encoded", double),
    ]
    if same is not None:
        out += [
            (f"call_url_{k}_inside_prefixes_of", inside),
            (f"call_url_{k}_outside_prefixes_of", outside),
        ]
    return out


def case(cid, why, effects, rules=(), values=None, refused=None, request=None):
    if request is None:
        request = {
            "profile": PROFILE,
            "observed_at": AT,
            "effects": list(effects),
            "url_rules": list(rules),
        }
    if refused:
        expect = {"status": "refused", "code": refused}
    else:
        expect = {"status": "ok", "values": [list(v) for v in values]}
    CASES.append({"id": cid, "why": why, "request": request, "expect": expect})


def origin(cid, why, raw, expected, path="/"):
    case(cid, why, [eff(raw)], values=ok(expected, path))


def refused_url(cid, why, raw, code, decoding="rfc3986", rules=(), **declared):
    """A URL the host does not read: the whole request is refused with this code."""
    case(cid, why, [eff(raw, decoding=decoding, **declared)], rules, refused=code)


def path(cid, why, raw_path, rfc, once, flag=False, double=False, **declared):
    """One written path under both profiles; an expectation may be ("refused", code)."""
    for decoding, expected in (("rfc3986", rfc), ("decode_all_once", once)):
        raw = "https://example.com" + raw_path
        if isinstance(expected, tuple):
            refused_url(f"{cid}__{decoding}", why, raw, expected[1], decoding, **declared)
        else:
            case(
                f"{cid}__{decoding}",
                why,
                [eff(raw, decoding=decoding, **declared)],
                values=ok(EX, expected, flag, double),
            )


# --- origin: scheme, host, port ---------------------------------------------------------------
origin("plain_https", "default port 443 made explicit", "https://example.com/", EX)
origin(
    "plain_http", "default port 80 made explicit", "http://example.com/", "http://example.com:80"
)
origin("ws_default", "ws defaults to 80", "ws://example.com/", "ws://example.com:80")
origin("wss_default", "wss defaults to 443", "wss://example.com/", "wss://example.com:443")
origin(
    "explicit_default_port",
    "an explicit default port is the same origin",
    "https://example.com:443/",
    EX,
)
origin(
    "explicit_other_port",
    "another port is another origin",
    "https://example.com:8443/",
    "https://example.com:8443",
)
origin(
    "http_on_443",
    "the scheme is part of the origin",
    "http://example.com:443/",
    "http://example.com:443",
)
origin("empty_port", "an empty port is the default (RFC 3986 3.2.3)", "https://example.com:/", EX)
origin(
    "port_leading_zeros",
    "a port is a decimal number",
    "http://example.com:080/",
    "http://example.com:80",
)
refused_url("port_zero", "no client connects to port 0", "http://example.com:0/", "bad_port")
origin("case_scheme_host", "scheme and host are case-insensitive", "HTTPS://Example.COM/", EX)
refused_url(
    "trailing_dot",
    "clients send another Host and SNI for example.com.",
    "https://example.com./",
    "trailing_dot_host",
)
refused_url(
    "trailing_dot_port",
    "trailing dot before a port",
    "https://example.com.:443/",
    "trailing_dot_host",
)
origin("userinfo", "userinfo is not the host", "https://user:pw@example.com/", EX)
origin(
    "userinfo_lookalike",
    "the host is what follows the @, not the familiar name before it",
    "https://example.com@evil.example/",
    "https://evil.example:443",
)
origin(
    "userinfo_lookalike_port",
    "a colon before the @ belongs to the userinfo",
    "https://example.com:443@evil.example:8443/",
    "https://evil.example:8443",
)
origin(
    "subdomain",
    "a subdomain is another host",
    "https://api.example.com/",
    "https://api.example.com:443",
)
origin(
    "suffix_lookalike",
    "a longer registered name that starts with the familiar one",
    "https://example.com.evil.example/",
    "https://example.com.evil.example:443",
)
origin("no_path", "an empty path is /", "https://example.com", EX)
origin("query_only", "a query ends the authority", "https://example.com?next=/a/../b", EX)
origin("fragment_only", "a fragment ends the authority", "https://example.com#/x", EX)
origin(
    "at_in_path",
    "an @ after the first slash is in the path",
    "https://example.com/@evil.example",
    EX,
    "/@evil.example",
)
origin(
    "underscore_label",
    "underscore labels exist in DNS",
    "https://a_b.example.com/",
    "https://a_b.example.com:443",
)
origin(
    "idn_unicode",
    "a non-ASCII host is compared as A-labels",
    "https://bücher.example/",
    "https://xn--bcher-kva.example:443",
)
origin(
    "idn_unicode_upper",
    "IDNA mapping lowercases",
    "https://B\u00dcCHER.example/",
    "https://xn--bcher-kva.example:443",
)
refused_url(
    "idn_unicode_dot",
    "a trailing dot after IDNA mapping",
    "https://b\u00fccher.example./",
    "trailing_dot_host",
)
origin(
    "idn_a_label",
    "an A-label written by the caller is kept",
    "https://xn--bcher-kva.example/",
    "https://xn--bcher-kva.example:443",
)
origin(
    "idn_a_label_upper",
    "A-labels are case-insensitive ASCII",
    "https://XN--BCHER-KVA.example/",
    "https://xn--bcher-kva.example:443",
)
origin(
    "idn_cyrillic_lookalike",
    "a Cyrillic a (U+0430) makes a different host than ASCII example.com",
    "https://exаmple.com/",
    "https://xn--exmple-4nf.com:443",
)
origin("idn_fullwidth", "IDNA 2003 maps a full-width letter to ASCII", "https://ｅxample.com/", EX)
origin(
    "idn_ideographic_dot",
    "IDNA 2003 reads U+3002 as a label separator",
    "https://example。com/",
    EX,
)
origin("ipv4", "a plain dotted quad", "https://127.0.0.1:8443/", "https://127.0.0.1:8443")
origin("ipv6", "an IPv6 literal with a port", "https://[::1]:8443/", "https://[::1]:8443")
origin(
    "ipv6_long",
    "an IPv6 literal takes its compressed form",
    "https://[0:0:0:0:0:0:0:1]/",
    "https://[::1]:443",
)
origin(
    "ipv6_upper", "IPv6 hex is lowercased", "https://[2001:DB8::A]/", "https://[2001:db8::a]:443"
)

# --- URLs the host does not read -----------------------------------------------------------------
refused_url("u_empty", "empty argument", "", "empty_url")
refused_url("u_long", "over the length limit", "https://example.com/" + "a" * 4096, "url_too_long")
refused_url("u_space", "a space inside the host", "https://exam ple.com/", "whitespace_or_control")
refused_url(
    "u_leading_space",
    "clients strip leading spaces",
    " https://example.com/",
    "whitespace_or_control",
)
refused_url("u_tab", "clients strip tabs", "https://exa\tmple.com/", "whitespace_or_control")
refused_url(
    "u_newline", "clients strip newlines", "https://example.com/\n", "whitespace_or_control"
)
refused_url(
    "u_backslash_authority",
    "\\ read as / by some clients",
    "https://example.com\\@evil.example/",
    "backslash",
)
refused_url("u_backslash_path", "\\ in a path", "https://example.com/a\\b", "backslash")
refused_url("u_relative", "a relative reference has no origin here", "/x/y", "no_scheme")
refused_url("u_scheme_relative", "a scheme-relative reference", "//example.com/x", "no_scheme")
refused_url("u_bare_host", "no scheme", "example.com/x", "no_scheme")
refused_url("u_ftp", "only http, https, ws, wss", "ftp://example.com/", "unsupported_scheme")
refused_url("u_file", "file URLs are path effects", "file:///etc/passwd", "unsupported_scheme")
refused_url("u_javascript", "no origin", "javascript:alert(1)", "unsupported_scheme")
refused_url("u_data", "no origin", "data:text/plain,hello", "unsupported_scheme")
refused_url(
    "u_host_port_only",
    "host:port without a scheme parses as a scheme",
    "example.com:443/x",
    "unsupported_scheme",
)
refused_url(
    "u_one_slash",
    "clients repair https:/host differently",
    "https:/example.com/",
    "missing_authority_slashes",
)
refused_url(
    "u_three_slashes",
    "clients repair https:///host differently",
    "https:///example.com/",
    "missing_authority_slashes",
)
refused_url("u_no_slashes", "no authority", "https:example.com", "missing_authority_slashes")
refused_url("u_empty_host", "no host", "https:///", "missing_authority_slashes")
refused_url("u_empty_host_path", "no host before the query", "https://?x", "empty_host")
refused_url("u_empty_host_port", "a port without a host", "https://:443/", "empty_host")
refused_url("u_userinfo_only", "userinfo without a host", "https://user@/", "empty_host")
refused_url(
    "u_two_ats",
    "two @: parsers pick different hosts",
    "https://a@b@example.com/",
    "ambiguous_userinfo",
)
refused_url(
    "u_pct_host", "a percent sequence in the host", "https://exa%6dple.com/", "percent_encoded_host"
)
refused_url(
    "u_sharp_s",
    "IDNA 2003 maps sharp s to ss, IDNA 2008 keeps it",
    "https://faß.example/",
    "idna_deviation_character",
)
refused_url("u_zwj", "a zero-width joiner", "https://exa‍mple.com/", "idna_deviation_character")
refused_url(
    "u_idna_bidi",
    "a label mixing left-to-right and right-to-left letters (RFC 3454 section 6)",
    "https://a\u05d0.example/",
    "idna_failed",
)
refused_url(
    "u_idna_prohibited",
    "U+FFFD is prohibited by Nameprep",
    "https://\ufffd.example/",
    "idna_failed",
)
refused_url(
    "u_bad_a_label",
    "an xn-- label that is not Punycode of a valid label",
    "https://xn--zzzzzz/",
    "bad_a_label",
)
refused_url("u_empty_label", "two dots", "https://a..example.com/", "bad_host")
refused_url(
    "u_two_trailing_dots", "two trailing dots", "https://example.com../", "trailing_dot_host"
)
refused_url(
    "u_leading_hyphen", "a label starting with a hyphen", "https://-a.example.com/", "bad_host"
)
refused_url(
    "u_host_char",
    "a character outside letters, digits, hyphen, underscore",
    "https://exa!mple.com/",
    "bad_host",
)
refused_url(
    "u_fullwidth_slash",
    "IDNA mapping yields a slash inside the host",
    "https://example.com／evil.example/",
    "bad_host",
)
refused_url("u_long_label", "a 64-character label", "https://" + "a" * 64 + ".example/", "bad_host")
refused_url("u_ipv6_open", "no closing bracket", "https://[::1/", "bad_host")
refused_url("u_ipv6_bad", "not an IPv6 literal", "https://[::g]/", "bad_host")
refused_url("u_ipv6_junk", "text after the bracket", "https://[::1]x/", "bad_host")
refused_url("u_ipv6_zone", "a zone id", "https://[fe80::1%25eth0]/", "ipv6_zone")
refused_url(
    "u_ipv4_short",
    "127.1 is 127.0.0.1 to some clients, a name to others",
    "https://127.1/",
    "ambiguous_ipv4_form",
)
refused_url("u_ipv4_hex", "hex octet", "https://0x7f.0.0.1/", "ambiguous_ipv4_form")
refused_url("u_ipv4_octal", "leading zero octet", "https://0177.0.0.1/", "ambiguous_ipv4_form")
refused_url("u_ipv4_integer", "one decimal integer", "https://2130706433/", "ambiguous_ipv4_form")
refused_url("u_ipv4_range", "an octet above 255", "https://1.2.3.256/", "ambiguous_ipv4_form")
refused_url("u_ipv4_five", "five numeric labels", "https://1.2.3.4.5/", "ambiguous_ipv4_form")
refused_url("u_port_text", "a port that is not a number", "https://example.com:https/", "bad_port")
refused_url("u_port_range", "a port above 65535", "https://example.com:65536/", "bad_port")
refused_url("u_port_sign", "a signed port", "https://example.com:+443/", "bad_port")
refused_url(
    "u_two_colons", "two colons after the host", "https://example.com:443:8443/", "bad_port"
)
refused_url(
    "u_port_fullwidth",
    "full-width digits are not ASCII digits",
    "https://example.com:４４３/",
    "bad_port",
)

# --- path: decoding profiles, dot segments, flags -----------------------------------------------
path("p_plain", "nothing to decode", "/x/y/a.txt", "/x/y/a.txt", "/x/y/a.txt")
path("p_dot_segments", "literal dot segments leave the subtree", "/x/y/../../z", "/z", "/z")
path("p_single_dot", ". is dropped", "/x/./y", "/x/y", "/x/y")
path("p_trailing_dot_segment", "a final . leaves a trailing slash", "/x/y/.", "/x/y/", "/x/y/")
path("p_trailing_dotdot", "a final .. leaves a trailing slash", "/x/y/..", "/x/", "/x/")
path("p_above_root", ".. never climbs above the root", "/../../a", "/a", "/a")
path("p_to_root", "everything popped", "/x/..", "/", "/")
path("p_trailing_slash", "a trailing slash is kept", "/x/y/", "/x/y/", "/x/y/")
path(
    "p_query_excluded",
    "query and fragment are not the path",
    "/x/y?next=/../z#/../w",
    "/x/y",
    "/x/y",
)
path(
    "p_encoded_dots_lower",
    "%2e is an unreserved dot in both profiles",
    "/x/y/%2e%2e/z",
    "/x/z",
    "/x/z",
    flag=True,
)
path("p_encoded_dots_upper", "hex case does not matter", "/x/y/%2E%2E/z", "/x/z", "/x/z", flag=True)
path(
    "p_encoded_dots_mixed",
    "one literal and one encoded dot",
    "/x/y/.%2e/z",
    "/x/z",
    "/x/z",
    flag=True,
)
path("p_encoded_single_dot", "an encoded . segment", "/x/%2e/y", "/x/y", "/x/y", flag=True)
path(
    "p_encoded_dot_in_name",
    "a dot inside a name is not a dot segment",
    "/x/a%2etxt",
    "/x/a.txt",
    "/x/a.txt",
)
path(
    "p_encoded_slash",
    "%2F is data under rfc3986 and a separator under decode_all_once",
    "/x/y%2F..%2F..%2Fz",
    "/x/y%2F..%2F..%2Fz",
    "/z",
    flag=True,
)
path(
    "p_encoded_slash_lower",
    "kept sequences get uppercase hex",
    "/x/y%2fa",
    "/x/y%2Fa",
    "/x/y/a",
    flag=True,
)
path(
    "p_encoded_slash_empty_segment",
    "a decoded slash can make an empty segment",
    "/x/%2Fy",
    "/x/%2Fy",
    ("refused", "empty_path_segment"),
    flag=True,
)
path(
    "p_encoded_backslash",
    "%5C is data under rfc3986",
    "/x/a%5cb",
    "/x/a%5Cb",
    ("refused", "encoded_backslash"),
    flag=True,
)
path(
    "p_double_dots",
    "%25 decodes to %, and nothing is decoded twice",
    "/x/y/%252e%252e/z",
    "/x/y/%252e%252e/z",
    "/x/y/%2e%2e/z",
    double=True,
)
path(
    "p_double_slash", "a doubly encoded slash", "/x/y%252Fz", "/x/y%252Fz", "/x/y%2Fz", double=True
)
path(
    "p_percent_literal",
    "%25 before non-hex text is a literal percent",
    "/x/100%25/y",
    "/x/100%25/y",
    "/x/100%/y",
)
path(
    "p_unreserved",
    "unreserved characters are decoded under rfc3986",
    "/x/%41%62%7E%2D%5F",
    "/x/Ab~-_",
    "/x/Ab~-_",
)
path(
    "p_reserved_kept",
    "a reserved character stays encoded under rfc3986",
    "/x/a%3Fb%23c%20d",
    "/x/a%3Fb%23c%20d",
    "/x/a?b#c d",
)
path("p_utf8", "UTF-8 sequences", "/x/%c3%a9", "/x/%C3%A9", "/x/é")
path("p_non_ascii_literal", "a literal non-ASCII character", "/x/é", "/x/%C3%A9", "/x/é")
path("p_case_sensitive", "path case is kept", "/X/y", "/X/y", "/X/y")
path(
    "p_semicolon",
    "servers of the Tomcat family strip ;v=1 from a segment",
    "/x/y;v=1/z",
    ("refused", "path_parameter"),
    ("refused", "path_parameter"),
)
path(
    "p_semicolon_encoded",
    "an encoded ; is refused as well: the host does not model which layer strips it",
    "/x/y%3Bv=1/z",
    ("refused", "path_parameter"),
    ("refused", "path_parameter"),
)
path(
    "p_semicolon_declared_data",
    "the tool profile declares ; as path data",
    "/x/y;v=1/z",
    "/x/y;v=1/z",
    "/x/y;v=1/z",
    semi=True,
)
path(
    "p_semicolon_encoded_declared_data",
    "declared data: %3B stays encoded under rfc3986 and is decoded under decode_all_once",
    "/x/y%3bv=1",
    "/x/y%3Bv=1",
    "/x/y;v=1",
    semi=True,
)
path(
    "p_disallowed_ascii",
    "ASCII that RFC 3986 does not allow in a path takes its percent form under rfc3986",
    '/x/a|b{c}^`"<>[]',
    "/x/a%7Cb%7Bc%7D%5E%60%22%3C%3E%5B%5D",
    '/x/a|b{c}^`"<>[]',
)
path(
    "p_disallowed_ascii_encoded",
    "the encoded spelling of the same name gives the same decoded path",
    "/x/a%7cb%7Bc%7D",
    "/x/a%7Cb%7Bc%7D",
    "/x/a|b{c}",
)
path(
    "p_sub_delims",
    "sub-delimiters, : and @ are allowed literally and stay as written",
    "/x/a!$&'()*+,=:@b",
    "/x/a!$&'()*+,=:@b",
    "/x/a!$&'()*+,=:@b",
)
path(
    "p_overlong_utf8",
    "an overlong encoding of / is not UTF-8 under either profile",
    "/x/..%c0%af..%c0%afz",
    ("refused", "non_utf8_path"),
    ("refused", "non_utf8_path"),
)
path(
    "p_encoded_surrogate",
    "a UTF-8 encoded surrogate is not UTF-8",
    "/x/%ed%a0%80",
    ("refused", "non_utf8_path"),
    ("refused", "non_utf8_path"),
)
path(
    "p_crlf",
    "an encoded CR LF",
    "/x/a%0d%0aHost:evil",
    ("refused", "encoded_control_character"),
    ("refused", "encoded_control_character"),
)
path(
    "p_c1_control",
    "an encoded C1 control (U+0085)",
    "/x/a%C2%85b",
    ("refused", "encoded_control_character"),
    ("refused", "encoded_control_character"),
)
path(
    "p_line_separator",
    "U+2028 would break the fact line where decoded text is shown",
    "/x/a%E2%80%A8b",
    "/x/a%E2%80%A8b",
    ("refused", "invisible_character"),
)
path(
    "p_bidi_override",
    "U+202E would reorder the shown text",
    "/x/%E2%80%AEtxt.exe",
    "/x/%E2%80%AEtxt.exe",
    ("refused", "invisible_character"),
)
path(
    "p_zero_width_space",
    "U+200B is invisible",
    "/x/ad%E2%80%8Bmin",
    "/x/ad%E2%80%8Bmin",
    ("refused", "invisible_character"),
)
path(
    "p_no_break_space",
    "U+00A0 looks like a space",
    "/x/a%C2%A0b",
    "/x/a%C2%A0b",
    ("refused", "invisible_character"),
)
path(
    "p_private_use",
    "U+E000 has no agreed meaning",
    "/x/a%EE%80%80b",
    "/x/a%EE%80%80b",
    ("refused", "invisible_character"),
)
path(
    "p_bidi_literal",
    "a literal U+202E in the URL",
    "/x/\u202etxt",
    "/x/%E2%80%AEtxt",
    ("refused", "invisible_character"),
)
path(
    "p_double_nul",
    "decoded a second time, the path holds a NUL",
    "/x/a%2500b",
    ("refused", "ambiguous_double_decoding"),
    ("refused", "ambiguous_double_decoding"),
)
path(
    "p_double_empty_segment",
    "decoded a second time, the path holds an empty segment",
    "/x/%252Fy",
    ("refused", "ambiguous_double_decoding"),
    ("refused", "ambiguous_double_decoding"),
)
path(
    "p_case_insensitive_plain",
    "a case-insensitive declaration does not change the decoded path",
    "/X/y",
    "/X/y",
    "/X/y",
    cs=False,
)
path(
    "p_case_insensitive_non_ascii",
    "case folding beyond ASCII is not reproduced",
    "/x/%c3%89",
    ("refused", "non_ascii_case_insensitive"),
    ("refused", "non_ascii_case_insensitive"),
    cs=False,
)
path(
    "p_case_insensitive_trailing_dot",
    "servers of the Windows family drop a trailing dot of a segment",
    "/x/admin./keys",
    ("refused", "trailing_dot_or_space_segment"),
    ("refused", "trailing_dot_or_space_segment"),
    cs=False,
)
path(
    "p_case_insensitive_encoded_dot",
    "the trailing dot may be encoded",
    "/x/admin%2e/keys",
    ("refused", "trailing_dot_or_space_segment"),
    ("refused", "trailing_dot_or_space_segment"),
    cs=False,
)
path(
    "p_case_insensitive_trailing_space",
    "and a trailing space",
    "/x/admin%20/keys",
    ("refused", "trailing_dot_or_space_segment"),
    ("refused", "trailing_dot_or_space_segment"),
    cs=False,
)
path(
    "p_case_insensitive_three_dots",
    "a segment of three dots",
    "/x/.../y",
    ("refused", "trailing_dot_or_space_segment"),
    ("refused", "trailing_dot_or_space_segment"),
    cs=False,
)
path(
    "p_case_sensitive_trailing_dot",
    "on a server declared case-sensitive these are ordinary names",
    "/x/admin./.../y",
    "/x/admin./.../y",
    "/x/admin./.../y",
)
path(
    "p_nul",
    "an encoded NUL: servers that truncate there read another path",
    "/x/a%00b",
    ("refused", "encoded_control_character"),
    ("refused", "encoded_control_character"),
)
path(
    "p_non_utf8",
    "bytes that are not UTF-8",
    "/x/a%ffb",
    ("refused", "non_utf8_path"),
    ("refused", "non_utf8_path"),
)
path(
    "p_bad_percent",
    "a % without two hex digits",
    "/x/a%2",
    ("refused", "bad_percent_encoding"),
    ("refused", "bad_percent_encoding"),
)
path(
    "p_bad_percent_hex",
    "a % before non-hex text",
    "/x/a%zz",
    ("refused", "bad_percent_encoding"),
    ("refused", "bad_percent_encoding"),
)
path(
    "p_empty_segment",
    "servers merge or keep //",
    "/x//y",
    ("refused", "empty_path_segment"),
    ("refused", "empty_path_segment"),
)
path(
    "p_leading_empty_segment",
    "a path that starts with //",
    "//x/y",
    ("refused", "empty_path_segment"),
    ("refused", "empty_path_segment"),
)

# --- comparison with policy rules ---------------------------------------------------------------
SUBTREE = rule("docs-subtree", (EX, "/x/y"))
WHOLE = rule("whole-origin", (EX, "/"))
OTHER = rule("other-origin", ("https://api.example.com:443", "/"))
SEND = rule("send-only", (EX, "/x/y"), ops=["send"])
RULES = [SUBTREE, WHOLE, OTHER, SEND]


def compared(
    cid,
    why,
    raw,
    decoded,
    inside,
    flag=False,
    double=False,
    decoding="rfc3986",
    op="fetch",
    refused=None,
    **declared,
):
    """Same origin as example.com; ``inside`` lists the selected rules that contain the URL."""
    effects = [eff(raw, op=op, decoding=decoding, **declared)]
    if refused:
        case(cid, why, effects, RULES, refused=refused)
        return
    selected = [r["rule_id"] for r in RULES if op in r["operations"]]
    same = [r for r in selected if r != "other-origin"]
    case(
        cid,
        why,
        effects,
        RULES,
        values=ok(
            EX,
            decoded,
            flag,
            double,
            same=same,
            diff=[r for r in selected if r not in same],
            inside=inside,
            outside=[r for r in selected if r not in inside],
        ),
    )


compared(
    "c_inside",
    "beneath the prefix",
    "https://example.com/x/y/a",
    "/x/y/a",
    ["docs-subtree", "whole-origin"],
)
compared(
    "c_equal",
    "equal to the prefix",
    "https://example.com/x/y",
    "/x/y",
    ["docs-subtree", "whole-origin"],
)
compared(
    "c_equal_slash",
    "the prefix with a trailing slash",
    "https://example.com/x/y/",
    "/x/y/",
    ["docs-subtree", "whole-origin"],
)
compared(
    "c_sibling_prefix",
    "/x/y-evil is not inside /x/y",
    "https://example.com/x/y-evil/a",
    "/x/y-evil/a",
    ["whole-origin"],
)
compared("c_parent", "the parent of the prefix", "https://example.com/x", "/x", ["whole-origin"])
compared(
    "c_dot_escape",
    "dot segments leave the subtree",
    "https://example.com/x/y/../../z",
    "/z",
    ["whole-origin"],
)
compared(
    "c_dot_return",
    "dot segments that stay inside",
    "https://example.com/x/y/a/../b",
    "/x/y/b",
    ["docs-subtree", "whole-origin"],
)
compared(
    "c_encoded_dot_escape",
    "encoded dot segments leave the subtree",
    "https://example.com/x/y/%2e%2e/%2E%2E/z",
    "/z",
    ["whole-origin"],
    flag=True,
)
compared("c_case", "path case differs", "https://example.com/X/Y/a", "/X/Y/a", ["whole-origin"])
compared(
    "c_encoded_letter",
    "an encoded unreserved letter is the same segment",
    "https://example.com/x/%79/a",
    "/x/y/a",
    ["docs-subtree", "whole-origin"],
)
compared(
    "c_encoded_slash_rfc",
    "under rfc3986 the encoded slashes are data, so the URL stays beneath /x/y",
    "https://example.com/x/y/a%2F..%2F..%2F..%2Fz",
    "/x/y/a%2F..%2F..%2F..%2Fz",
    ["docs-subtree", "whole-origin"],
    flag=True,
)
compared(
    "c_encoded_slash_once",
    "under decode_all_once the same URL leaves the subtree",
    "https://example.com/x/y/a%2F..%2F..%2F..%2Fz",
    "/z",
    ["whole-origin"],
    flag=True,
    decoding="decode_all_once",
)
compared(
    "c_double_once",
    "decoded once it stays beneath /x/y, decoded twice it is /z: the lists would change",
    "https://example.com/x/y/%252e%252e/%252e%252e/z",
    None,
    None,
    decoding="decode_all_once",
    refused="ambiguous_double_decoding",
)
compared(
    "c_double_rfc",
    "the same URL under rfc3986: a second full decoding still changes the lists",
    "https://example.com/x/y/%252e%252e/%252e%252e/z",
    None,
    None,
    refused="ambiguous_double_decoding",
)
compared(
    "c_double_harmless",
    "double encoded, but both readings are beneath /x/y: the facts stand and the flag says so",
    "https://example.com/x/y/a%2541",
    "/x/y/a%41",
    ["docs-subtree", "whole-origin"],
    double=True,
    decoding="decode_all_once",
)
compared(
    "c_double_harmless_rfc",
    "the same under rfc3986, where %25 stays encoded",
    "https://example.com/x/y/a%2541",
    "/x/y/a%2541",
    ["docs-subtree", "whole-origin"],
    double=True,
)
compared(
    "c_double_into_prefix",
    "decoded twice the first segment becomes x: the lists would change",
    "https://example.com/%2578/y/a",
    None,
    None,
    decoding="decode_all_once",
    refused="ambiguous_double_decoding",
)
compared(
    "c_case_insensitive_inside",
    "a server declared case-insensitive: /X/Y is the declared /x/y",
    "https://example.com/X/Y/a",
    "/X/Y/a",
    ["docs-subtree", "whole-origin"],
    cs=False,
)
compared(
    "c_case_insensitive_once",
    "the same under decode_all_once, with an encoded letter",
    "https://example.com/%58/y/A",
    "/X/y/A",
    ["docs-subtree", "whole-origin"],
    decoding="decode_all_once",
    cs=False,
)
compared(
    "c_case_insensitive_sibling",
    "case folding does not make a sibling a child",
    "https://example.com/X/Y-evil/a",
    "/X/Y-evil/a",
    ["whole-origin"],
    cs=False,
)
compared(
    "c_semicolon",
    "a ..; segment would stay beneath /x/y as text and leave it on a Tomcat-family server",
    "https://example.com/x/y/..;/..;/z",
    None,
    None,
    refused="path_parameter",
)
compared(
    "c_semicolon_declared_data",
    "declared data: ..; is an ordinary name",
    "https://example.com/x/y/..;/z",
    "/x/y/..;/z",
    ["docs-subtree", "whole-origin"],
    semi=True,
)
compared(
    "c_operation",
    "a rule for send joins the lists of a send",
    "https://example.com/x/y/a",
    "/x/y/a",
    ["docs-subtree", "whole-origin", "send-only"],
    op="send",
)
compared(
    "c_default_port",
    "an explicit default port is the same origin",
    "https://EXAMPLE.com:443/x/y/a",
    "/x/y/a",
    ["docs-subtree", "whole-origin"],
)
case(
    "c_other_port",
    "another port is another origin: inside no prefix",
    [eff("https://example.com:8443/x/y/a")],
    RULES,
    values=ok(
        "https://example.com:8443",
        "/x/y/a",
        same=[],
        diff=["docs-subtree", "whole-origin", "other-origin"],
        inside=[],
        outside=["docs-subtree", "whole-origin", "other-origin"],
    ),
)
case(
    "c_other_scheme",
    "http is another origin than https",
    [eff("http://example.com/x/y/a")],
    RULES,
    values=ok(
        "http://example.com:80",
        "/x/y/a",
        same=[],
        diff=["docs-subtree", "whole-origin", "other-origin"],
        inside=[],
        outside=["docs-subtree", "whole-origin", "other-origin"],
    ),
)
case(
    "c_userinfo_lookalike",
    "the familiar name is userinfo; the host is another origin",
    [eff("https://example.com@api.example.com/x/y/a")],
    RULES,
    values=ok(
        "https://api.example.com:443",
        "/x/y/a",
        same=["other-origin"],
        diff=["docs-subtree", "whole-origin"],
        inside=["other-origin"],
        outside=["docs-subtree", "whole-origin"],
    ),
)
case(
    "c_idn_lookalike",
    "a look-alike host is a different origin from every rule",
    [eff("https://exаmple.com/x/y/a")],
    RULES,
    values=ok(
        "https://xn--exmple-4nf.com:443",
        "/x/y/a",
        same=[],
        diff=["docs-subtree", "whole-origin", "other-origin"],
        inside=[],
        outside=["docs-subtree", "whole-origin", "other-origin"],
    ),
)
case(
    "c_two_effects",
    "two readable URL effects: facts in inventory order",
    [
        eff("https://api.example.com/v1", op="send", arg="/call/arguments/to"),
        eff("http://example.com/x", arg="/call/arguments/from"),
    ],
    [OTHER],
    values=ok(
        "https://api.example.com:443",
        "/v1",
        same=["other-origin"],
        diff=[],
        inside=["other-origin"],
        outside=[],
    )
    + ok(
        "http://example.com:80",
        "/x",
        same=[],
        diff=["other-origin"],
        inside=[],
        outside=["other-origin"],
        k=2,
    ),
)
case(
    "c_two_effects_second_refused",
    "the second effect cannot be read: the whole request is refused, not half answered",
    [
        eff("https://api.example.com/v1", op="send", arg="/call/arguments/to"),
        eff("https://127.1/x", arg="/call/arguments/from"),
    ],
    [OTHER],
    refused="ambiguous_ipv4_form",
)
case(
    "c_refused_with_rules",
    "an unreadable URL refuses the request, with rules",
    [eff("https://example.com/x//y")],
    RULES,
    refused="empty_path_segment",
)
case(
    "c_refused_uncovered_operation",
    "an unreadable URL refuses the request even when no rule covers its operation",
    [eff("https://example.com/x//y", op="open")],
    [SEND],
    refused="empty_path_segment",
)
case(
    "c_uncovered_operation",
    "no rule covers open: all four lists are present and empty",
    [eff("https://example.com/x/y", op="open")],
    [SEND],
    values=ok(EX, "/x/y", same=[], diff=[], inside=[], outside=[]),
)
case("c_no_effects", "a call with no URL effect has no URL fact", [], RULES, values=[])

# --- the 2026-11-22 independent review: its probe policy and every input it reported ----------
FILES = "https://files.example.com:443"
PARTNER = "https://partner.example.net:443"
PROBE = [
    rule("uploads", (FILES, "/team/uploads"), ops=["send"]),
    rule("no-admin", (FILES, "/admin")),
    rule("partner", (PARTNER, "/"), ops=["send"]),
    rule("no-local", ("http://127.0.0.1:8080", "/")),
]
PROBE_IDS = ["uploads", "no-admin", "partner", "no-local"]


def rv_refused(cid, why, raw, code, decoding="rfc3986", **declared):
    case(
        f"rv_{cid}", why, [eff(raw, op="send", decoding=decoding, **declared)], PROBE, refused=code
    )


def rv(cid, why, raw, origin, decoded, same, inside, flag=False, double=False, **declared):
    case(
        f"rv_{cid}",
        why,
        [eff(raw, op="send", **declared)],
        PROBE,
        values=ok(
            origin,
            decoded,
            flag,
            double,
            same=same,
            diff=[r for r in PROBE_IDS if r not in same],
            inside=inside,
            outside=[r for r in PROBE_IDS if r not in inside],
        ),
    )


F = "https://files.example.com"
BOTH = ["uploads", "no-admin"]
# fix 2: host characters newer than Unicode 3.2
rv_refused(
    "modifier_small_a",
    "U+1D43: UTS 46 clients reach partner.example.net",
    "https://p\u1d43rtner.example.net/",
    "idna_unassigned_character",
)
rv_refused(
    "squared_a",
    "U+1F130: UTS 46 clients reach partner.example.net",
    "https://p\U0001f130rtner.example.net/",
    "idna_unassigned_character",
)
rv_refused(
    "subscript_j",
    "U+2C7C: UTS 46 clients reach files.examjple.com",
    "https://files.exam\u2c7cple.com/",
    "idna_unassigned_character",
)
rv_refused(
    "capital_sharp_s",
    "U+1E9E: IDNA 2003 folds it to ss, UTS 46 clients reach the sharp-s name",
    "https://fa\u1e9e.example/",
    "idna_unassigned_character",
)
# fix 3: path parameters
rv_refused(
    "tomcat_dotdot_semicolon",
    "the ..;/ escape from the allowed subtree",
    F + "/team/uploads/..;/..;/admin/keys",
    "path_parameter",
)
rv_refused(
    "semicolon_on_deny_prefix",
    "/admin;x is /admin on a Tomcat-family server",
    F + "/admin;x/keys",
    "path_parameter",
)
rv_refused(
    "semicolon_on_deny_prefix_once",
    "the same under decode_all_once",
    F + "/admin;x/keys",
    "path_parameter",
    "decode_all_once",
)
rv_refused(
    "encoded_dotdot_semicolon",
    "%2e%2e%3b decodes to ..;",
    F + "/team/uploads/%2e%2e%3b/admin",
    "path_parameter",
    "decode_all_once",
)
# fix 4: a second decoding, and bytes that are not readable text
rv_refused(
    "double_dots_allow_side",
    "inside uploads once, /admin/keys twice",
    F + "/team/uploads/%252e%252e/%252e%252e/admin/keys",
    "ambiguous_double_decoding",
    "decode_all_once",
)
rv_refused(
    "double_slash_allow_side",
    "inside uploads once, /admin twice",
    F + "/team/uploads/..%252f..%252fadmin",
    "ambiguous_double_decoding",
    "decode_all_once",
)
rv_refused(
    "double_deny_prefix",
    "outside no-admin once, /admin/keys twice",
    F + "/%2561dmin/keys",
    "ambiguous_double_decoding",
    "decode_all_once",
)
rv_refused(
    "overlong_slash_rfc",
    "overlong UTF-8 for / under rfc3986",
    F + "/team/uploads/..%C0%AF..%C0%AFadmin",
    "non_utf8_path",
)
rv_refused(
    "overlong_slash_once",
    "and under decode_all_once",
    F + "/team/uploads/..%c0%af..%c0%afadmin",
    "non_utf8_path",
    "decode_all_once",
)
rv_refused(
    "nul_rfc", "a NUL under rfc3986", F + "/team/uploads/..%00/admin", "encoded_control_character"
)
rv_refused(
    "crlf_rfc",
    "CR LF under rfc3986",
    F + "/team/uploads/a%0d%0aHost:evil",
    "encoded_control_character",
)
rv_refused(
    "encoded_surrogate",
    "an encoded surrogate",
    F + "/team/uploads/%ed%a0%80",
    "non_utf8_path",
    "decode_all_once",
)
rv_refused(
    "percent_u",
    "%u002e is not a percent sequence",
    F + "/team/uploads/%u002e%u002e/admin",
    "bad_percent_encoding",
)
# decision (b): the tool profile is trusted to say that the server keeps %2F and %5C as data
rv(
    "encoded_slash_rfc_allow_side",
    "rfc3986 as declared: the encoded slashes are data",
    F + "/team/uploads/..%2F..%2Fadmin/keys",
    FILES,
    "/team/uploads/..%2F..%2Fadmin/keys",
    BOTH,
    ["uploads"],
    flag=True,
)
rv(
    "encoded_slash_rfc_deny_side",
    "rfc3986 as declared: /admin%2Fkeys is one segment",
    F + "/admin%2Fkeys",
    FILES,
    "/admin%2Fkeys",
    BOTH,
    [],
    flag=True,
)
rv(
    "encoded_backslash_rfc",
    "rfc3986 as declared: %5C is data",
    F + "/team/uploads/..%5C..%5Cadmin",
    FILES,
    "/team/uploads/..%5C..%5Cadmin",
    BOTH,
    ["uploads"],
    flag=True,
)
# fix 5: ASCII characters not allowed in a path
rv("pipe_encoded", "the encoded spelling", F + "/a%7Cb/x", FILES, "/a%7Cb/x", BOTH, [])
rv(
    "pipe_literal",
    "the literal spelling gives the same decoded path",
    F + "/a|b/x",
    FILES,
    "/a%7Cb/x",
    BOTH,
    [],
)
# fix 6: decoded text that could forge or hide fact text
rv_refused(
    "line_separator_forgery",
    "U+2028 followed by text shaped like a fact line",
    F + "/team/uploads/x%E2%80%A8%20-%20call_url_1_double_encoded:%20false",
    "invisible_character",
    "decode_all_once",
)
rv_refused(
    "bidi_override",
    "U+202E in the shown path",
    F + "/team/uploads/%E2%80%AEtxt.exe",
    "invisible_character",
    "decode_all_once",
)
# fix 7: IPv6 literals that are IPv4 addresses
rv_refused(
    "ipv4_mapped",
    "the socket reaches 127.0.0.1:8080",
    "http://[::ffff:127.0.0.1]:8080/",
    "ipv6_embedded_ipv4",
)
rv_refused(
    "ipv4_mapped_hex",
    "the same address in hex groups",
    "http://[::FFFF:7F00:1]:8080/",
    "ipv6_embedded_ipv4",
)
rv_refused(
    "ipv4_compatible",
    "the deprecated compatible form",
    "http://[::127.0.0.1]:8080/",
    "ipv6_embedded_ipv4",
)
rv(
    "ipv6_loopback",
    "::1 is not an embedded IPv4 address",
    "http://[::1]:8080/",
    "http://[::1]:8080",
    "/",
    [],
    [],
)
rv("ipv6_unspecified", "nor is ::", "http://[::]:8080/", "http://[::]:8080", "/", [], [])
# fix 8: path case and Windows-family segments follow the declaration; a trailing host dot refuses
rv(
    "upper_deny_prefix_sensitive",
    "declared case-sensitive: /ADMIN is not /admin",
    F + "/ADMIN/keys",
    FILES,
    "/ADMIN/keys",
    BOTH,
    [],
)
rv(
    "upper_deny_prefix_insensitive",
    "declared case-insensitive: /ADMIN is /admin",
    F + "/ADMIN/keys",
    FILES,
    "/ADMIN/keys",
    BOTH,
    ["no-admin"],
    cs=False,
)
rv_refused(
    "iis_trailing_dot",
    "/admin. is /admin on a Windows-family server",
    F + "/admin./keys",
    "trailing_dot_or_space_segment",
    cs=False,
)
rv_refused(
    "three_dots",
    "a ... segment",
    F + "/team/uploads/.../admin",
    "trailing_dot_or_space_segment",
    cs=False,
)
rv_refused(
    "dotdot_space",
    "a segment of .. and a space",
    F + "/team/uploads/..%20/admin",
    "trailing_dot_or_space_segment",
    "decode_all_once",
    cs=False,
)
rv_refused(
    "trailing_dot_host",
    "partner.example.net. is another Host and SNI",
    "https://partner.example.net./",
    "trailing_dot_host",
)
rv_refused(
    "ideographic_dots_trailing",
    "U+3002 separators with a trailing one",
    "https://partner\u3002example\u3002net\u3002/",
    "trailing_dot_host",
)
# what the review verified as right, pinned
rv(
    "userinfo_lookalike",
    "the host follows the @",
    "https://partner.example.net@evil.example/",
    "https://evil.example:443",
    "/",
    [],
    [],
)
rv(
    "percent_in_userinfo",
    "%40 inside userinfo is not an @",
    "https://partner.example.net%40evil.example@x.example/",
    "https://x.example:443",
    "/",
    [],
    [],
)
rv(
    "fragment_then_at",
    "a # ends the authority",
    "https://partner.example.net#@evil.example/",
    PARTNER,
    "/",
    ["partner"],
    ["partner"],
)
rv(
    "query_path_like",
    "a query is not the path",
    F + "/team/uploads?/../admin",
    FILES,
    "/team/uploads",
    BOTH,
    ["uploads"],
)
rv(
    "port_leading_zeros",
    "000443 is 443",
    "https://partner.example.net:000443/",
    PARTNER,
    "/",
    ["partner"],
    ["partner"],
)
rv(
    "empty_port",
    "an empty port is the default",
    "https://partner.example.net:/",
    PARTNER,
    "/",
    ["partner"],
    ["partner"],
)
rv_refused("port_zero", "port 0", "https://partner.example.net:0/", "bad_port")
rv_refused("ipv6_zone", "a zone id", "http://[fe80::1%25en0]:8080/", "ipv6_zone")
rv_refused(
    "semicolon_in_authority",
    "a ; in the host",
    "https://partner.example.net;evil.example/",
    "bad_host",
)
rv_refused("hex_last_label", "1.2.3.0x4", "https://1.2.3.0x4/", "ambiguous_ipv4_form")
rv_refused("numeric_last_label", "example.123", "https://example.123/", "ambiguous_ipv4_form")
rv_refused(
    "a_label_of_sharp_s",
    "an A-label IDNA 2003 would never produce",
    "https://xn--fa-hia.example/",
    "bad_a_label",
)
rv(
    "mixed_a_u_label",
    "an A-label and a U-label in one host",
    "https://xn--bcher-kva.b\u00fccher.example/",
    "https://xn--bcher-kva.xn--bcher-kva.example:443",
    "/",
    [],
    [],
)
rv(
    "soft_hyphen",
    "U+00AD is mapped to nothing by IDNA 2003 and by UTS 46",
    "https://part\u00adner.example.net/",
    PARTNER,
    "/",
    ["partner"],
    ["partner"],
)
rv(
    "zero_width_space_host",
    "U+200B likewise",
    "https://part\u200bner.example.net/",
    PARTNER,
    "/",
    ["partner"],
    ["partner"],
)
rv(
    "fullwidth_digits_ipv4",
    "full-width digits map to ASCII in both",
    "http://\uff11\uff12\uff17.0.0.1:8080/",
    "http://127.0.0.1:8080",
    "/",
    ["no-local"],
    ["no-local"],
)
rv(
    "zero_address",
    "0.0.0.0 is a plain dotted quad and its own origin",
    "http://0.0.0.0:8080/",
    "http://0.0.0.0:8080",
    "/",
    [],
    [],
)
rv(
    "one_dot_leader",
    "U+2024 maps to a dot in IDNA 2003; WHATWG and httpx reject the URL (optional 8)",
    "https://partner\u2024example.net/",
    PARTNER,
    "/",
    ["partner"],
    ["partner"],
)
case(
    "rv_decoded_question_and_hash",
    "decoded ? and # are path data under decode_all_once",
    [eff(F + "/team/uploads/a%3Fb%23c", op="send", decoding="decode_all_once")],
    PROBE,
    values=ok(
        FILES,
        "/team/uploads/a?b#c",
        same=BOTH,
        diff=["partner", "no-local"],
        inside=["uploads"],
        outside=["no-admin", "partner", "no-local"],
    ),
)
case(
    "rv_uncovered_open_refused",
    "an unreadable URL refuses although no probe rule set covers it differently for open",
    [eff("https://files.example.com/team//x", op="open")],
    [PROBE[0]],
    refused="empty_path_segment",
)

# --- refusals: the request itself is malformed ---------------------------------------------------
GOOD = {
    "profile": PROFILE,
    "observed_at": AT,
    "effects": [eff("https://example.com/")],
    "url_rules": [],
}


def refuse(cid, why, code="bad_request", **change):
    request = json.loads(json.dumps(GOOD))
    for key, value in change.items():
        if value is None:
            del request[key]
        else:
            request[key] = value
    case(cid, why, None, refused=code, request=request)


refuse("r_profile", "another profile", profile="armor-hostfacts-paths-v1")
refuse("r_missing_key", "no observed_at", observed_at=None)
refuse("r_extra_key", "an unknown key", runtime={})
refuse("r_observed_at", "a timestamp without an offset", observed_at="2026-11-22T12:00:00")
refuse("r_effects_type", "effects is not a list", effects={})
refuse(
    "r_too_many_effects",
    "nine effects",
    effects=[eff("https://example.com/", arg=f"/call/arguments/u{i}") for i in range(9)],
)
refuse("r_raw_type", "raw is not a string", effects=[eff(None)])
refuse("r_operation", "an unknown operation", effects=[eff("https://example.com/", op="read")])
refuse(
    "r_decoding",
    "an unknown decoding profile",
    effects=[eff("https://example.com/", decoding="decode_twice")],
)
refuse(
    "r_pointer", "argument is not a JSON pointer", effects=[eff("https://example.com/", arg="url")]
)
refuse(
    "r_duplicate_pointer",
    "two effects for one argument",
    effects=[eff("https://example.com/"), eff("https://example.com/")],
)
refuse(
    "r_effect_keys",
    "an effect with an unknown key",
    effects=[dict(eff("https://example.com/"), final_link="follow")],
)
refuse(
    "r_effect_missing_declaration",
    "an effect without the case declaration",
    effects=[{k: v for k, v in eff("https://example.com/").items() if k != "case_sensitive"}],
)
refuse(
    "r_case_type",
    "case_sensitive is not a boolean",
    effects=[dict(eff("https://example.com/"), case_sensitive="yes")],
)
refuse(
    "r_semicolon_type",
    "semicolon_is_data is not a boolean",
    effects=[dict(eff("https://example.com/"), semicolon_is_data=None)],
)
refuse(
    "r_rule_origin_port_zero",
    "an origin with port 0",
    code="bad_url_rules",
    url_rules=[rule("a", ("https://example.com:0", "/"))],
)
refuse(
    "r_rule_path_character",
    "a prefix path with a character outside letters, digits and - . _ ~",
    code="bad_url_rules",
    url_rules=[rule("a", (EX, "/a|b"))],
)
refuse("r_rules_type", "url_rules is not a list", code="bad_url_rules", url_rules={})
refuse(
    "r_rule_keys",
    "a rule in policy-file form, not compiled",
    code="bad_url_rules",
    url_rules=[{"id": "a", "urls": {"within": "https://example.com/"}}],
)
refuse("r_rule_duplicate", "duplicate rule ids", code="bad_url_rules", url_rules=[WHOLE, WHOLE])
refuse(
    "r_rule_origin_form",
    "an origin without its port",
    code="bad_url_rules",
    url_rules=[rule("a", ("https://example.com", "/"))],
)
refuse(
    "r_rule_origin_case",
    "an origin that is not lowercased",
    code="bad_url_rules",
    url_rules=[rule("a", ("https://Example.com:443", "/"))],
)
refuse(
    "r_rule_path_slash",
    "a prefix path with a trailing slash",
    code="bad_url_rules",
    url_rules=[rule("a", (EX, "/x/"))],
)
refuse(
    "r_rule_path_dots",
    "a prefix path with a dot segment",
    code="bad_url_rules",
    url_rules=[rule("a", (EX, "/x/../y"))],
)
refuse(
    "r_rule_operations",
    "an empty operations list",
    code="bad_url_rules",
    url_rules=[rule("a", (EX, "/"), ops=[])],
)


# --- policy files -------------------------------------------------------------------------------
def policy(cid, why, rules, compiled=None, error=None):
    expect = (
        {"status": "error", "code": error} if error else {"status": "ok", "url_rules": compiled}
    )
    POLICY_CASES.append({"id": cid, "why": why, "rules": rules, "expect": expect})


def line(rid, urls=None, tier="allow"):
    row = {"id": rid, "tier": tier, "description": f"Rule {rid}."}
    return row if urls is None else dict(row, urls=urls)


policy(
    "plain_forms",
    "an origin, a prefix, a list and a default for; rules without urls are skipped",
    [
        line("origin", {"within": "https://example.com"}),
        line("prose"),
        line("prefix", {"within": "https://API.Example.com:443/v1/", "for": ["send"]}),
        line(
            "several",
            {
                "within": ["http://localhost:8080/", "wss://example.com/live"],
                "for": ["open", "fetch"],
            },
        ),
    ],
    [
        rule("origin", (EX, "/")),
        rule("prefix", ("https://api.example.com:443", "/v1"), ops=["send"]),
        rule(
            "several",
            ("http://localhost:8080", "/"),
            ("wss://example.com:443", "/live"),
            ops=["open", "fetch"],
        ),
    ],
)
policy(
    "idn_origin",
    "a non-ASCII policy host is compiled to A-labels",
    [line("a", {"within": "https://bücher.example/"})],
    [rule("a", ("https://xn--bcher-kva.example:443", "/"))],
)
policy("no_url_rules", "a policy with no urls field", [line("a"), line("b")], [])
policy("e_not_list", "the policy is not a list", {"id": "a"}, error="bad_policy")
policy("e_duplicate_id", "duplicate rule ids", [line("a"), line("a")], error="bad_policy")
policy(
    "e_urls_string",
    "urls is not an object",
    [line("a", "https://example.com/")],
    error="bad_urls_field",
)
policy(
    "e_unknown_key",
    "an unknown key in urls",
    [line("a", {"within": "https://example.com/", "origin": "x"})],
    error="bad_urls_field",
)
policy("e_no_within", "within is required", [line("a", {"for": ["send"]})], error="bad_urls_field")
policy(
    "e_empty_within", "within lists nothing", [line("a", {"within": []})], error="bad_urls_field"
)
policy(
    "e_nine_urls",
    "more than eight URLs",
    [line("a", {"within": [f"https://h{i}.example/" for i in range(9)]})],
    error="bad_urls_field",
)
policy(
    "e_bad_operation",
    "an unknown operation",
    [line("a", {"within": "https://example.com/", "for": ["write"]})],
    error="bad_urls_field",
)
policy(
    "e_listed_twice",
    "one prefix written two ways",
    [line("a", {"within": ["https://example.com", "HTTPS://example.com:443/"]})],
    error="bad_urls_field",
)
policy(
    "e_relative",
    "a host without a scheme",
    [line("a", {"within": "example.com"})],
    error="bad_prefix",
)
policy(
    "e_wildcard",
    "a wildcard host",
    [line("a", {"within": "https://*.example.com/"})],
    error="bad_prefix",
)
policy(
    "e_userinfo",
    "userinfo in a prefix",
    [line("a", {"within": "https://user@example.com/"})],
    error="bad_prefix",
)
policy(
    "e_query",
    "a query in a prefix",
    [line("a", {"within": "https://example.com/x?y=1"})],
    error="bad_prefix",
)
policy(
    "e_fragment",
    "a fragment in a prefix",
    [line("a", {"within": "https://example.com/x#y"})],
    error="bad_prefix",
)
policy(
    "e_percent",
    "a percent sequence in a prefix",
    [line("a", {"within": "https://example.com/a%20b/"})],
    error="bad_prefix",
)
policy(
    "e_dot_segment",
    "a dot segment in a prefix",
    [line("a", {"within": "https://example.com/x/../y/"})],
    error="bad_prefix",
)
policy(
    "e_empty_segment",
    "an empty segment in a prefix",
    [line("a", {"within": "https://example.com/x//y/"})],
    error="bad_prefix",
)
policy(
    "e_trailing_dot",
    "a host ending in a dot",
    [line("a", {"within": "https://example.com./"})],
    error="bad_prefix",
)
policy(
    "e_non_ascii_path",
    "a non-ASCII prefix path",
    [line("a", {"within": "https://example.com/café/"})],
    error="bad_prefix",
)
policy(
    "e_ipv4_short",
    "an ambiguous numeric host",
    [line("a", {"within": "https://127.1/"})],
    error="bad_prefix",
)
policy(
    "e_pipe",
    "a character RFC 3986 does not allow in a path",
    [line("a", {"within": "https://files.example.com/a|b"})],
    error="bad_prefix",
)
policy(
    "e_colon_at",
    "sub-delimiters, : and @ have two spellings under rfc3986",
    [line("a", {"within": "https://example.com/a:b@c"})],
    error="bad_prefix",
)
policy(
    "e_plus",
    "a + in a prefix",
    [line("a", {"within": "https://example.com/a+b"})],
    error="bad_prefix",
)
policy(
    "e_semicolon",
    "a ; in a prefix",
    [line("a", {"within": "https://example.com/a;b"})],
    error="bad_prefix",
)
policy(
    "e_brace",
    "braces in a prefix",
    [line("a", {"within": "https://files.example.com/a{b}"})],
    error="bad_prefix",
)
policy(
    "e_port_zero", "port 0", [line("a", {"within": "https://example.com:0"})], error="bad_prefix"
)
policy(
    "e_ipv4_mapped",
    "an IPv4-mapped IPv6 origin",
    [line("a", {"within": "https://[::ffff:127.0.0.1]:8080"})],
    error="bad_prefix",
)
policy(
    "e_unassigned_host",
    "a host character newer than Unicode 3.2",
    [line("a", {"within": "https://p\u1d43rtner.example.net"})],
    error="bad_prefix",
)
policy(
    "e_capital_sharp_s",
    "U+1E9E in a policy host",
    [line("a", {"within": "https://fa\u1e9e.example"})],
    error="bad_prefix",
)
policy(
    "e_within_number", "within is not a string", [line("a", {"within": 443})], error="bad_prefix"
)
policy(
    "e_within_empty", "within is an empty string", [line("a", {"within": ""})], error="bad_prefix"
)
policy(
    "e_within_surrogate",
    "within holds a lone surrogate",
    [line("a", {"within": "https://example.com/\ud800"})],
    error="bad_prefix",
)
policy(
    "e_rule_not_object", "a policy line that is not an object", [line("a"), "b"], error="bad_policy"
)
policy(
    "upper_and_tilde",
    "the characters a prefix path may hold; its case is kept",
    [line("a", {"within": "https://example.com/Team_A/v1.2/~x-y/"})],
    [rule("a", (EX, "/Team_A/v1.2/~x-y"))],
)
policy(
    "e_too_many",
    "more than 32 rules with urls",
    [line(f"r{i}", {"within": "https://example.com/"}) for i in range(33)],
    error="too_many_url_rules",
)


def main():
    ids = [c["id"] for c in CASES] + [c["id"] for c in POLICY_CASES]
    assert len(ids) == len(set(ids)), "duplicate case id"
    vectors = {
        "profile": PROFILE,
        "status": "Hand-written expectations. The Python reference and any port must reproduce "
        "every case exactly.",
        "cases": CASES,
        "policy_cases": POLICY_CASES,
    }
    data = (json.dumps(vectors, ensure_ascii=True, indent=1) + "\n").encode("ascii")
    (HERE / "vectors.json").write_bytes(data)
    reference = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[2] / "src/hostfacts/urls.py"
    )
    manifest = {
        "profile": PROFILE,
        "reference_python_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "vectors_sha256": hashlib.sha256(data).hexdigest(),
        "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": len(CASES),
        "policy_cases": len(POLICY_CASES),
        "copies": {
            "kai": [
                "(proposed) train/kai/hostfacts/urls.py",
                "(proposed) docs/contracts/armor-hostfacts-urls-v1/",
            ],
            "agentarmor": ["src/hostfacts/urls.py", "tests/fixtures/armor-hostfacts-urls-v1/"],
        },
    }
    (HERE / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"{len(CASES)} cases, {len(POLICY_CASES)} policy cases")


if __name__ == "__main__":
    main()
