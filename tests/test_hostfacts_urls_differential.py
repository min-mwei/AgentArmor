"""URL host facts against Python's own URL code: `urllib.parse`, `posixpath` and `ipaddress`.

CPU only, standard library only, no network. The hand-written vectors encode this profile's
model of URLs; this file checks that model against an independent parser. Where the two agree
the test says so for every row. Where they differ, the difference is intended, and each row of
INTENDED states what the standard library does and why the host refuses or reads otherwise.

The standard library is lenient by design (it repairs and accepts). A host fact must not be:
whenever a mainstream client or server could reach another resource than the fact would claim,
the host refuses.
"""

import importlib.util
import ipaddress
import posixpath
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "agentarmor_hostfacts_urls_differential", ROOT / "src/hostfacts/urls.py"
)
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)

DEFAULT = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def stdlib_origin(raw):
    """scheme://host:port as urllib reads it, with the scheme default made explicit."""
    parts = urlsplit(raw)
    host = parts.hostname
    if ":" in host:
        host = f"[{ipaddress.IPv6Address(host).compressed}]"
    port = parts.port if parts.port is not None else DEFAULT[parts.scheme]
    return f"{parts.scheme}://{host}:{port}"


def host_code(raw):
    try:
        U.split(raw)
    except U.Refusal as refusal:
        return refusal.code
    return None


AGREE = [
    "https://example.com/",
    "http://example.com/a/b?q=1#f",
    "HTTPS://Example.COM/A",
    "https://example.com:443/",
    "https://example.com:8443/x",
    "http://example.com:080/",
    "https://example.com:/",
    "https://user:pw@example.com/",
    "https://example.com@evil.example/",
    "https://example.com:443@evil.example:8443/",
    "https://partner.example.net%40evil.example@x.example/",
    "https://example.com?next=/a/../b",
    "https://example.com#@evil.example/",
    "https://example.com/@evil.example",
    "https://a_b.example.com/",
    "https://xn--bcher-kva.example/",
    "https://127.0.0.1:8443/",
    "https://[::1]:8443/",
    "https://[2001:DB8:0:0:0:0:0:A]/",
    "ws://example.com/live",
    "wss://example.com/live",
]


@pytest.mark.parametrize("raw", AGREE)
def test_origin_agrees_with_urllib_on_ordinary_urls(raw):
    assert U.split(raw)["origin"] == stdlib_origin(raw)
    assert U.split(raw)["path"] == urlsplit(raw).path


# (URL, what urllib.parse gives for hostname and port, the host's refusal code, why).
INTENDED = [
    (
        "https://example.com./",
        ("example.com.", None),
        "trailing_dot_host",
        "urllib keeps the dot. Clients send another Host header and SNI for it, so it is neither "
        "safely the same origin as example.com nor safely a different one.",
    ),
    (
        "https://exa\tmple.com/",
        ("example.com", None),
        "whitespace_or_control",
        "urllib silently removes tab, CR and LF (as WHATWG clients do); other clients fail or "
        "truncate. The host does not choose.",
    ),
    (
        " https://example.com/",
        ("example.com", None),
        "whitespace_or_control",
        "urllib strips leading spaces and controls.",
    ),
    (
        "https://example.com\\@evil.example/",
        ("evil.example", None),
        "backslash",
        "urllib reads the backslash as userinfo data, so the host is evil.example; WHATWG "
        "clients read it as / and reach example.com.",
    ),
    (
        "https://a@b@example.com/",
        ("example.com", None),
        "ambiguous_userinfo",
        "urllib takes the host after the last @; other parsers take the first.",
    ),
    (
        "https://127.1/",
        ("127.1", None),
        "ambiguous_ipv4_form",
        "urllib treats it as a name; WHATWG clients and inet_aton connect to 127.0.0.1.",
    ),
    (
        "https://0x7f.0.0.1/",
        ("0x7f.0.0.1", None),
        "ambiguous_ipv4_form",
        "as above, with a hexadecimal first number.",
    ),
    (
        "https://2130706433/",
        ("2130706433", None),
        "ambiguous_ipv4_form",
        "as above, the address written as one integer.",
    ),
    (
        "http://[::ffff:127.0.0.1]:8080/",
        ("::ffff:127.0.0.1", 8080),
        "ipv6_embedded_ipv4",
        "urllib and ipaddress accept it as an IPv6 host; the socket reaches 127.0.0.1.",
    ),
    (
        "https://exa%6dple.com/",
        ("exa%6dple.com", None),
        "percent_encoded_host",
        "urllib leaves the percent sequence in the host; WHATWG clients decode it.",
    ),
    (
        "https://faß.example/",
        ("faß.example", None),
        "idna_deviation_character",
        "urllib does no IDNA at all; IDNA 2003 and 2008 clients reach different names.",
    ),
    (
        "https://pᵃrtner.example.net/",
        ("pᵃrtner.example.net", None),
        "idna_unassigned_character",
        "urllib does no IDNA; UTS 46 clients map U+1D43 to a and reach partner.example.net.",
    ),
    (
        "https://example.com:0/",
        ("example.com", 0),
        "bad_port",
        "urllib accepts port 0; no client connects to it.",
    ),
    (
        "https:/example.com/",
        (None, None),
        "missing_authority_slashes",
        "urllib reads a URL with no host and the path /example.com/; WHATWG clients repair it "
        "to https://example.com/.",
    ),
    (
        "//example.com/x",
        ("example.com", None),
        "no_scheme",
        "urllib accepts a scheme-relative reference; its origin depends on a base URL.",
    ),
    (
        "ftp://example.com/",
        ("example.com", None),
        "unsupported_scheme",
        "urllib parses any scheme; only http, https, ws and wss have an origin here.",
    ),
]


@pytest.mark.parametrize(
    "raw, stdlib, code, why",
    INTENDED,
    ids=[row[2] + "/" + str(i) for i, row in enumerate(INTENDED)],
)
def test_every_intended_difference_from_urllib_is_a_refusal_with_a_reason(raw, stdlib, code, why):
    parts = urlsplit(raw)
    assert (parts.hostname, parts.port) == stdlib  # what the standard library does, pinned
    assert host_code(raw) == code and len(why) > 20


def test_urllib_rejects_what_the_host_rejects_for_ports_and_ipv6():
    for raw, code in (
        ("https://example.com:65536/", "bad_port"),
        ("https://example.com:https/", "bad_port"),
        ("https://[::1/", "bad_host"),
        ("https://[::g]/", "bad_host"),
    ):
        assert host_code(raw) == code
        with pytest.raises(ValueError):
            urlsplit(raw).port  # noqa: B018 (urlsplit or .port raises)


def test_idna_differs_from_urllib_by_design():
    # urllib returns the Unicode host untouched; the host compares A-labels, so a look-alike of
    # an ASCII name is a different origin and an IDN equals its own A-label spelling.
    raw = "https://bücher.example/"
    assert urlsplit(raw).hostname == "bücher.example"
    assert U.split(raw)["origin"] == "https://xn--bcher-kva.example:443"
    assert U.split(raw)["origin"] == U.split("https://xn--bcher-kva.example/")["origin"]
    assert U.split("https://exаmple.com/")["origin"] != U.split("https://example.com/")["origin"]


def test_ipv6_literals_that_carry_an_ipv4_address_are_what_ipaddress_says_they_are():
    """ipaddress parses all of these as ordinary IPv6 hosts, and so does urllib. The host
    refuses them: a dual stack, a NAT64 gateway or a tunnel delivers the request to the IPv4
    address inside, which an exact-origin rule on that IPv4 address would not name."""
    compatible = ipaddress.IPv6Network("::/96")
    translated = ipaddress.IPv6Network("::ffff:0:0:0/96")
    nat64 = (ipaddress.IPv6Network("64:ff9b::/96"), ipaddress.IPv6Network("64:ff9b:1::/48"))
    carriers = (
        "::ffff:1.2.3.4",
        "::ffff:7f00:1",
        "::1.2.3.4",
        "::7f00:1",
        "::2",
        "::ffff:0:cb00:7105",
        "64:ff9b::cb00:7105",
        "64:ff9b:1::cb00:7105",
        "2002:cb00:7105::1",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",
    )
    for literal in carriers:
        address = ipaddress.IPv6Address(literal)
        assert (
            address.ipv4_mapped is not None
            or address.sixtofour is not None
            or address.teredo is not None
            or address in compatible
            or address in translated
            or any(address in block for block in nat64)
        )
        assert urlsplit(f"http://[{literal}]/").hostname == literal  # urllib accepts it
        assert host_code(f"http://[{literal}]/") == "ipv6_embedded_ipv4"
    for literal in ("::1", "2001:db8::1", "64:ff9a::1", "2003::1", "fe80::1"):
        address = ipaddress.IPv6Address(literal)
        assert address.ipv4_mapped is None and address.sixtofour is None and address.teredo is None
        assert U.split(f"http://[{literal}]/")["origin"] == stdlib_origin(f"http://[{literal}]/")


def test_the_unspecified_addresses_parse_in_the_standard_library_and_are_refused():
    assert ipaddress.IPv4Address("0.0.0.0").is_unspecified
    assert ipaddress.IPv6Address("::").is_unspecified
    assert urlsplit("http://0.0.0.0:8080/").hostname == "0.0.0.0"
    for raw in ("http://0.0.0.0:8080/", "http://[::]:8080/", "http://[0:0:0:0:0:0:0:0]/"):
        assert host_code(raw) == "unspecified_address"


def stdlib_path(path):
    """unquote, then posixpath.normpath, with the trailing slash RFC 3986 keeps."""
    text = unquote(path, errors="strict") or "/"
    normal = posixpath.normpath(text)
    normal = "/" + normal.lstrip("/")  # POSIX keeps a leading //, a URL path does not
    keep = text.endswith(("/", "/.", "/..")) and normal != "/"
    return normal + ("/" if keep else "")


PATHS = [
    "/",
    "/x/y/a.txt",
    "/x/y/../../z",
    "/x/./y",
    "/x/y/.",
    "/x/y/..",
    "/../../a",
    "/x/..",
    "/x/y/",
    "/x/y/%2e%2e/z",
    "/x/y/.%2E/z",
    "/x/%2e/y",
    "/x/a%2etxt",
    "/x/y%2F..%2F..%2Fz",
    "/x/y%2fa",
    "/x/100%25/y",
    "/x/%41%62%7E%2D%5F",
    "/x/a%3Fb%23c%20d",
    "/x/%c3%a9",
    "/x/a%7Cb",
    "/x/a|b",
    "/X/y",
]


@pytest.mark.parametrize("path", PATHS)
def test_decode_all_once_agrees_with_unquote_then_normpath(path):
    assert U.read_path(path, "decode_all_once")["decoded_path"] == stdlib_path(path)


@pytest.mark.parametrize("path", PATHS)
def test_rfc3986_agrees_with_decode_all_once_unless_a_reserved_octet_is_encoded(path):
    """The two profiles are one reading when the written path encodes only unreserved
    characters: fully decoding the rfc3986 form then gives the decode_all_once form. They
    differ exactly when a percent sequence stands for a separator, which the flag fact reports."""
    rfc = U.read_path(path, "rfc3986")
    once = U.read_path(path, "decode_all_once")
    if rfc["encoded_separators"]:
        assert rfc["decoded_path"] != once["decoded_path"]
    else:
        assert unquote(rfc["decoded_path"], errors="strict") == once["decoded_path"]


# (path, what unquote/normpath give, the host's refusal code under decode_all_once, why).
PATH_INTENDED = [
    ("/x//y", "/x/y", "empty_path_segment", "normpath merges //; servers merge or keep it."),
    ("/x/y;v=1/z", "/x/y;v=1/z", "path_parameter", "the stdlib keeps ;v=1; Tomcat strips it."),
    ("/x/a%00b", "/x/a\x00b", "encoded_control_character", "unquote yields a NUL."),
    ("/x/a%5Cb", "/x/a\\b", "encoded_backslash", "unquote yields a backslash."),
    (
        "/x/a%E2%80%A8b",
        "/x/a b",
        "invisible_character",
        "unquote yields a line separator, which would break a fact line.",
    ),
    (
        "/x/%252e%252e/%2500",
        "/x/%2e%2e/%00",
        "ambiguous_double_decoding",
        "unquote decodes once and stops; a second decoding gives a NUL.",
    ),
    (
        "/x/%25252e%25252e/y",
        "/x/%252e%252e/y",
        "ambiguous_double_decoding",
        "unquote decodes once; the third decoding gives a .. segment.",
    ),
    (
        "/x/%F0%9F%98%80",
        "/x/\U0001f600",
        "invisible_character",
        "unquote yields U+1F600, which Unicode 3.2 does not assign; the host uses that fixed "
        "table so that every Python refuses or answers alike.",
    ),
]


@pytest.mark.parametrize(
    "path, stdlib, code, why",
    PATH_INTENDED,
    ids=[f"{row[2]}/{i}" for i, row in enumerate(PATH_INTENDED)],
)
def test_every_intended_path_difference_is_a_refusal_with_a_reason(path, stdlib, code, why):
    assert stdlib_path(path) == stdlib and len(why) > 20
    with pytest.raises(U.Refusal) as refused:
        U.read_path(path, "decode_all_once")
    assert refused.value.code == code


def test_bytes_that_are_not_utf8_are_replaced_by_unquote_and_refused_by_the_host():
    # unquote's default turns bad bytes into U+FFFD, a guess; overlong forms are bad bytes.
    for path in ("/x/a%ffb", "/x/..%c0%af..%c0%afz", "/x/%ed%a0%80"):
        assert "�" in unquote(path)
        for decoding in U.DECODINGS:
            with pytest.raises(U.Refusal) as refused:
                U.read_path(path, decoding)
            assert refused.value.code == "non_utf8_path"


def test_a_percent_sign_without_two_hex_digits_is_kept_by_unquote_and_refused_by_the_host():
    for path in ("/x/a%2", "/x/a%zz", "/x/%u002e"):
        assert unquote(path) == path
        with pytest.raises(U.Refusal) as refused:
            U.read_path(path, "rfc3986")
        assert refused.value.code == "bad_percent_encoding"
