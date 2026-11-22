"""Writes vectors.json and MANIFEST.json for armor-hostfacts-urls-v1 from hand-written cases.

The expected values below were written by hand from RFC 3986 (syntax, percent-encoding, dot
segments), RFC 6454 (origin) and the profile's stated rules. This builder never imports or calls
the reference implementation, so the vectors are an independent check of the Python reference and
of any port. The two A-label strings are the Punycode of their hosts (RFC 3492).
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


def eff(raw, op="fetch", decoding="rfc3986", arg="/call/arguments/url"):
    return {"argument": arg, "raw": raw, "operation": op, "decoding": decoding}


def rule(rid, *prefixes, ops=ALL):
    return {
        "rule_id": rid,
        "prefixes": [{"origin": o, "path": p} for o, p in prefixes],
        "operations": list(ops),
    }


def ok(
    origin, path, flag=False, double=False, same=None, diff=None, inside=None, outside=None, k=1
):
    """The facts of readable URL effect k; the four lists only when the policy has URL rules."""
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


def bad(code, k=1):
    return [(f"call_url_{k}_unusable", code)]


def case(cid, why, effects, rules=(), values=None, unusable=(), refused=None, request=None):
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
        expect = {
            "status": "ok",
            "values": [list(v) for v in values],
            "unusable": list(unusable),
        }
    CASES.append({"id": cid, "why": why, "request": request, "expect": expect})


def origin(cid, why, raw, expected, path="/"):
    case(cid, why, [eff(raw)], values=ok(expected, path))


def unusable(cid, why, raw, code, decoding="rfc3986"):
    case(cid, why, [eff(raw, decoding=decoding)], values=bad(code), unusable=[1])


def path(cid, why, raw_path, rfc, once, flag=False, double=False):
    """One written path under both profiles; ``once`` may be ("unusable", code)."""
    for decoding, expected in (("rfc3986", rfc), ("decode_all_once", once)):
        raw = "https://example.com" + raw_path
        if isinstance(expected, tuple):
            unusable(f"{cid}__{decoding}", why, raw, expected[1], decoding)
        else:
            case(
                f"{cid}__{decoding}",
                why,
                [eff(raw, decoding=decoding)],
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
origin("port_zero", "port 0 is a number", "http://example.com:0/", "http://example.com:0")
origin("case_scheme_host", "scheme and host are case-insensitive", "HTTPS://Example.COM/", EX)
origin("trailing_dot", "one trailing dot names the same host", "https://example.com./", EX)
origin("trailing_dot_port", "trailing dot before a port", "https://example.com.:443/", EX)
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
    "idn_unicode_upper_dot",
    "IDNA mapping lowercases; trailing dot removed",
    "https://BÜCHER.example./",
    "https://xn--bcher-kva.example:443",
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
unusable("u_empty", "empty argument", "", "empty_url")
unusable("u_long", "over the length limit", "https://example.com/" + "a" * 4096, "url_too_long")
unusable("u_space", "a space inside the host", "https://exam ple.com/", "whitespace_or_control")
unusable(
    "u_leading_space",
    "clients strip leading spaces",
    " https://example.com/",
    "whitespace_or_control",
)
unusable("u_tab", "clients strip tabs", "https://exa\tmple.com/", "whitespace_or_control")
unusable("u_newline", "clients strip newlines", "https://example.com/\n", "whitespace_or_control")
unusable(
    "u_backslash_authority",
    "\\ read as / by some clients",
    "https://example.com\\@evil.example/",
    "backslash",
)
unusable("u_backslash_path", "\\ in a path", "https://example.com/a\\b", "backslash")
unusable("u_relative", "a relative reference has no origin here", "/x/y", "no_scheme")
unusable("u_scheme_relative", "a scheme-relative reference", "//example.com/x", "no_scheme")
unusable("u_bare_host", "no scheme", "example.com/x", "no_scheme")
unusable("u_ftp", "only http, https, ws, wss", "ftp://example.com/", "unsupported_scheme")
unusable("u_file", "file URLs are path effects", "file:///etc/passwd", "unsupported_scheme")
unusable("u_javascript", "no origin", "javascript:alert(1)", "unsupported_scheme")
unusable("u_data", "no origin", "data:text/plain,hello", "unsupported_scheme")
unusable(
    "u_host_port_only",
    "host:port without a scheme parses as a scheme",
    "example.com:443/x",
    "unsupported_scheme",
)
unusable(
    "u_one_slash",
    "clients repair https:/host differently",
    "https:/example.com/",
    "missing_authority_slashes",
)
unusable(
    "u_three_slashes",
    "clients repair https:///host differently",
    "https:///example.com/",
    "missing_authority_slashes",
)
unusable("u_no_slashes", "no authority", "https:example.com", "missing_authority_slashes")
unusable("u_empty_host", "no host", "https:///", "missing_authority_slashes")
unusable("u_empty_host_path", "no host before the query", "https://?x", "empty_host")
unusable("u_empty_host_port", "a port without a host", "https://:443/", "empty_host")
unusable("u_userinfo_only", "userinfo without a host", "https://user@/", "empty_host")
unusable(
    "u_two_ats",
    "two @: parsers pick different hosts",
    "https://a@b@example.com/",
    "ambiguous_userinfo",
)
unusable(
    "u_pct_host", "a percent sequence in the host", "https://exa%6dple.com/", "percent_encoded_host"
)
unusable(
    "u_sharp_s",
    "IDNA 2003 maps sharp s to ss, IDNA 2008 keeps it",
    "https://faß.example/",
    "idna_deviation_character",
)
unusable("u_zwj", "a zero-width joiner", "https://exa‍mple.com/", "idna_deviation_character")
unusable(
    "u_idna_bidi",
    "a label mixing left-to-right and right-to-left letters (RFC 3454 section 6)",
    "https://a\u05d0.example/",
    "idna_failed",
)
unusable(
    "u_idna_prohibited",
    "U+FFFD is prohibited by Nameprep",
    "https://\ufffd.example/",
    "idna_failed",
)
unusable(
    "u_bad_a_label",
    "an xn-- label that is not Punycode of a valid label",
    "https://xn--zzzzzz/",
    "bad_a_label",
)
unusable("u_empty_label", "two dots", "https://a..example.com/", "bad_host")
unusable("u_two_trailing_dots", "two trailing dots", "https://example.com../", "bad_host")
unusable(
    "u_leading_hyphen", "a label starting with a hyphen", "https://-a.example.com/", "bad_host"
)
unusable(
    "u_host_char",
    "a character outside letters, digits, hyphen, underscore",
    "https://exa!mple.com/",
    "bad_host",
)
unusable(
    "u_fullwidth_slash",
    "IDNA mapping yields a slash inside the host",
    "https://example.com／evil.example/",
    "bad_host",
)
unusable("u_long_label", "a 64-character label", "https://" + "a" * 64 + ".example/", "bad_host")
unusable("u_ipv6_open", "no closing bracket", "https://[::1/", "bad_host")
unusable("u_ipv6_bad", "not an IPv6 literal", "https://[::g]/", "bad_host")
unusable("u_ipv6_junk", "text after the bracket", "https://[::1]x/", "bad_host")
unusable("u_ipv6_zone", "a zone id", "https://[fe80::1%25eth0]/", "ipv6_zone")
unusable(
    "u_ipv4_short",
    "127.1 is 127.0.0.1 to some clients, a name to others",
    "https://127.1/",
    "ambiguous_ipv4_form",
)
unusable("u_ipv4_hex", "hex octet", "https://0x7f.0.0.1/", "ambiguous_ipv4_form")
unusable("u_ipv4_octal", "leading zero octet", "https://0177.0.0.1/", "ambiguous_ipv4_form")
unusable("u_ipv4_integer", "one decimal integer", "https://2130706433/", "ambiguous_ipv4_form")
unusable("u_ipv4_range", "an octet above 255", "https://1.2.3.256/", "ambiguous_ipv4_form")
unusable("u_ipv4_five", "five numeric labels", "https://1.2.3.4.5/", "ambiguous_ipv4_form")
unusable("u_port_text", "a port that is not a number", "https://example.com:https/", "bad_port")
unusable("u_port_range", "a port above 65535", "https://example.com:65536/", "bad_port")
unusable("u_port_sign", "a signed port", "https://example.com:+443/", "bad_port")
unusable("u_two_colons", "two colons after the host", "https://example.com:443:8443/", "bad_port")
unusable(
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
    ("unusable", "empty_path_segment"),
    flag=True,
)
path(
    "p_encoded_backslash",
    "%5C is data under rfc3986",
    "/x/a%5cb",
    "/x/a%5Cb",
    ("unusable", "encoded_backslash"),
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
path("p_semicolon", "path parameters are literal text", "/x/y;v=1/z", "/x/y;v=1/z", "/x/y;v=1/z")
path("p_nul", "an encoded NUL", "/x/a%00b", "/x/a%00b", ("unusable", "encoded_control_character"))
path(
    "p_non_utf8", "bytes that are not UTF-8", "/x/a%ffb", "/x/a%FFb", ("unusable", "non_utf8_path")
)
path(
    "p_bad_percent",
    "a % without two hex digits",
    "/x/a%2",
    ("unusable", "bad_percent_encoding"),
    ("unusable", "bad_percent_encoding"),
)
path(
    "p_bad_percent_hex",
    "a % before non-hex text",
    "/x/a%zz",
    ("unusable", "bad_percent_encoding"),
    ("unusable", "bad_percent_encoding"),
)
path(
    "p_empty_segment",
    "servers merge or keep //",
    "/x//y",
    ("unusable", "empty_path_segment"),
    ("unusable", "empty_path_segment"),
)
path(
    "p_leading_empty_segment",
    "a path that starts with //",
    "//x/y",
    ("unusable", "empty_path_segment"),
    ("unusable", "empty_path_segment"),
)

# --- comparison with policy rules ---------------------------------------------------------------
SUBTREE = rule("docs-subtree", (EX, "/x/y"))
WHOLE = rule("whole-origin", (EX, "/"))
OTHER = rule("other-origin", ("https://api.example.com:443", "/"))
SEND = rule("send-only", (EX, "/x/y"), ops=["send"])
RULES = [SUBTREE, WHOLE, OTHER, SEND]


def compared(
    cid, why, raw, decoded, inside, flag=False, double=False, decoding="rfc3986", op="fetch"
):
    """Same origin as example.com; ``inside`` lists the selected rules that contain the URL."""
    selected = [r["rule_id"] for r in RULES if op in r["operations"]]
    same = [r for r in selected if r != "other-origin"]
    case(
        cid,
        why,
        [eff(raw, op=op, decoding=decoding)],
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
    "decoded once, %252e%252e is the literal segment %2e%2e and stays beneath /x/y",
    "https://example.com/x/y/%252e%252e/%252e%252e/z",
    "/x/y/%2e%2e/%2e%2e/z",
    ["docs-subtree", "whole-origin"],
    double=True,
    decoding="decode_all_once",
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
    "two URL effects, the second unusable: its facts are one unusable fact",
    [
        eff("https://api.example.com/v1", op="send", arg="/call/arguments/to"),
        eff("https://127.1/x", arg="/call/arguments/from"),
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
    + bad("ambiguous_ipv4_form", k=2),
    unusable=[2],
)
case(
    "c_unusable_with_rules",
    "an unusable URL is in no list, even with rules",
    [eff("https://example.com/x//y")],
    RULES,
    values=bad("empty_path_segment"),
    unusable=[1],
)
case("c_no_effects", "a call with no URL effect has no URL fact", [], RULES, values=[])

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
