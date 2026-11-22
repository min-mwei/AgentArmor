"""Python reference for host-computed URL facts (armor-hostfacts-urls-v1); CPU only, no model.

The hand-written vectors in `tests/fixtures/armor-hostfacts-urls-v1/` are built by
`make_vectors.py`, which never imports the reference. The design note is `docs/url_hostfacts.md`.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/armor-hostfacts-urls-v1"
SOURCE = ROOT / "src/hostfacts/urls.py"
VECTORS = json.loads((FIXTURE / "vectors.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((FIXTURE / "MANIFEST.json").read_text(encoding="utf-8"))
AT = "2026-11-22T12:00:00Z"

_spec = importlib.util.spec_from_file_location("agentarmor_hostfacts_urls", SOURCE)
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def request(effects, rules=()):
    return {
        "profile": U.PROFILE,
        "observed_at": AT,
        "effects": list(effects),
        "url_rules": list(rules),
    }


def effect(raw, operation="fetch", decoding="rfc3986", case_sensitive=True, semicolon=False):
    return {
        "argument": "/call/arguments/url",
        "raw": raw,
        "operation": operation,
        "decoding": decoding,
        "case_sensitive": case_sensitive,
        "semicolon_is_data": semicolon,
    }


def values(out):
    return {f["name"]: f["value"] for f in out["facts"]}


def note(out, name):
    return next(f["note"] for f in out["facts"] if f["name"] == name)


def test_manifest_pins_the_reference_and_the_fixture():
    assert sha(SOURCE) == MANIFEST["reference_python_sha256"]
    assert sha(FIXTURE / "vectors.json") == MANIFEST["vectors_sha256"]
    assert sha(FIXTURE / "make_vectors.py") == MANIFEST["builder_sha256"]
    assert len(VECTORS["cases"]) == MANIFEST["cases"]
    assert len(VECTORS["policy_cases"]) == MANIFEST["policy_cases"]
    assert VECTORS["profile"] == MANIFEST["profile"] == U.PROFILE


def test_the_vector_builder_never_imports_the_reference():
    builder = (FIXTURE / "make_vectors.py").read_text(encoding="utf-8")
    imported = [line for line in builder.split("\n") if line.startswith(("import ", "from "))]
    assert imported == ["import hashlib", "import json", "import sys", "from pathlib import Path"]
    assert "importlib" not in builder and "exec(" not in builder


def test_the_new_kind_leaves_the_path_and_measure_references_as_pinned():
    # A new kind is a new profile beside the others: neither existing reference file changes.
    for kind in ("paths", "measures"):
        manifest = ROOT / f"tests/fixtures/armor-hostfacts-{kind}-v1/MANIFEST.json"
        pinned = json.loads(manifest.read_text(encoding="utf-8"))["reference_python_sha256"]
        assert sha(ROOT / f"src/hostfacts/{kind}.py") == pinned


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_hand_written_vector(case):
    out = U.compute_or_refusal(case["request"])
    expect = case["expect"]
    if expect["status"] == "refused":
        assert (out["status"], out.get("code")) == ("refused", expect["code"])
        assert set(out) == {"profile", "status", "code", "detail"}
        return
    assert out["status"] == "ok", out
    assert set(out) == {"profile", "status", "facts"}  # as paths.py: no other key
    assert [[f["name"], f["value"]] for f in out["facts"]] == expect["values"]
    for fact in out["facts"]:
        assert set(fact) == {"name", "value", "unit", "provenance", "as_of", "note"}
        assert (fact["unit"], fact["provenance"]) == (None, "host_computed")
        assert fact["as_of"] == case["request"]["observed_at"] and fact["note"].endswith(".")
        assert "unusable" not in fact["name"]


@pytest.mark.parametrize(
    "case", VECTORS["policy_cases"], ids=[c["id"] for c in VECTORS["policy_cases"]]
)
def test_hand_written_policy_case(case):
    expect = case["expect"]
    if expect["status"] == "error":
        with pytest.raises(U.PolicyError) as exc:
            U.compile_policy(case["rules"])
        assert exc.value.code == expect["code"] and exc.value.hint
    else:
        compiled = U.compile_policy(case["rules"])
        assert compiled == expect["url_rules"]
        assert U.compute(request([], compiled))["facts"] == []  # compiled rules are accepted


def test_every_refusal_code_and_policy_error_has_a_vector():
    refused = {c["expect"]["code"] for c in VECTORS["cases"] if c["expect"]["status"] == "refused"}
    assert refused == set(U.REFUSAL_CODES)
    errors = {
        c["expect"]["code"] for c in VECTORS["policy_cases"] if c["expect"]["status"] == "error"
    }
    assert errors == set(U.POLICY_ERRORS)


def test_an_unreadable_url_refuses_the_request_and_never_becomes_a_fact():
    """The user's decision: AgentArmor refuses; Kai is never shown an "unusable" fact."""
    rules = U.compile_policy([{"id": "docs", "urls": {"within": "https://example.com/"}}])
    made = request([effect("https://example.com\\@evil.example/")], rules)
    with pytest.raises(U.Refusal) as refused:
        U.compute(made)
    assert refused.value.code == "backslash" and "evil" not in refused.value.detail
    assert U.compute_or_refusal(made) == {
        "profile": U.PROFILE,
        "status": "refused",
        "code": "backslash",
        "detail": "the URL holds a backslash",
    }
    assert not hasattr(U, "Unusable") and not hasattr(U, "UNUSABLE_CODES")
    # Whether or not any rule covers the operation, and whatever the effect's position.
    assert U.compute_or_refusal(request([effect("https://127.1/", "open")]))["status"] == "refused"
    second = dict(effect("https://127.1/"), argument="/call/arguments/other")
    out = U.compute_or_refusal(request([effect("https://example.com/"), second], rules))
    assert (out["status"], out["code"]) == ("refused", "ambiguous_ipv4_form")


def test_a_policy_url_the_host_cannot_read_is_a_load_time_policy_error():
    for text in ("https://example.com./", "https://127.1/", "https://example.com/a\\b"):
        with pytest.raises(U.PolicyError) as exc:
            U.compile_policy([{"id": "r", "urls": {"within": text}}])
        assert exc.value.code == "bad_prefix" and exc.value.rule_id == "r"
        assert exc.value.hint.startswith("code ")


@pytest.mark.parametrize(
    "raw, steps",
    [
        ("https://example.com:8443/", ["explicit port 8443 kept"]),
        ("https://example.com/", ["no port written: default port 443 for https"]),
        ("https://example.com:/", ["empty port: default port 443 for https"]),
        (
            "http://example.com:080/",
            ["explicit port 80 kept (leading zeros dropped); it is the default for http"],
        ),
        (
            "HTTPS://user@Example.COM:443/",
            [
                "uppercase scheme letters lowercased",
                "userinfo before @ is not part of the origin and was ignored",
                "uppercase host letters lowercased",
                "explicit port 443 kept; it is the default for https",
            ],
        ),
        (
            "https://bücher.example/",
            [
                "non-ASCII host mapped by IDNA 2003 ToASCII (A-labels, xn--)",
                "no port written: default port 443 for https",
            ],
        ),
        (
            "https://xn--bcher-kva.example/",
            [
                "host has IDNA A-labels (xn--), compared as written",
                "no port written: default port 443 for https",
            ],
        ),
        (
            "https://[2001:DB8:0:0:0:0:0:A]:8443/",
            ["IPv6 literal written in its compressed lowercase form", "explicit port 8443 kept"],
        ),
    ],
)
def test_the_origin_note_names_every_normalization_step_that_changed_something(raw, steps):
    out = U.compute(request([effect(raw)]))
    assert out["facts"][0]["name"] == "call_url_1_origin"
    assert out["facts"][0]["note"] == (
        "Host reading of call argument /call/arguments/url for fetch as scheme://host:port, "
        "without network access: scheme and host lowercased, userinfo ignored, non-ASCII host "
        "as IDNA A-labels, the scheme's default port when none is written. Applied here: "
        + "; ".join(steps)
        + "."
    )


def test_the_path_note_states_the_one_decoding_profile_and_never_the_other():
    for decoding, other in (("rfc3986", "decode_all_once"), ("decode_all_once", "rfc3986")):
        out = U.compute(request([effect("https://example.com/../a%2Fb", decoding=decoding)]))
        told = note(out, "call_url_1_decoded_path")
        assert f"decoding profile {decoding}:" in told and other not in told
        assert told.endswith("Applied here: a .. segment at the root was dropped.")
    out = U.compute(request([effect("https://example.com")]))
    assert note(out, "call_url_1_decoded_path") == (
        "Path of call argument /call/arguments/url for fetch under decoding profile rfc3986: "
        "percent sequences of unreserved characters (letters, digits, - . _ ~) decoded, every "
        "other percent sequence kept with uppercase hex, non-ASCII characters and ASCII "
        "characters not allowed in a path written as UTF-8 percent sequences, so an encoded / "
        "(%2F) stays inside its segment; then . segments dropped and .. popping one segment, "
        "never above the root. Query and fragment excluded. Applied here: empty path read as /."
    )
    out = U.compute(request([effect("https://example.com/a", decoding="decode_all_once")]))
    assert note(out, "call_url_1_decoded_path") == (
        "Path of call argument /call/arguments/url for fetch under decoding profile "
        "decode_all_once: every percent sequence decoded exactly once as UTF-8, a decoded / "
        "separates segments, and any percent sequence left is literal text that is not decoded "
        "again, so any ? or # shown is decoded data; then . segments dropped and .. popping one "
        "segment, never above the root. Query and fragment excluded. Applied here: nothing "
        "changed."
    )


def test_the_notes_say_what_the_tool_profile_declared():
    rules = U.compile_policy([{"id": "r", "urls": {"within": "https://example.com/x"}}])
    for sensitive, words in ((True, "case-sensitive"), (False, "ASCII case-insensitive")):
        out = U.compute(
            request([effect("https://example.com/X/a", case_sensitive=sensitive)], rules)
        )
        assert note(out, "call_url_1_inside_prefixes_of") == (
            "Policy rules with a declared URL prefix of origin call_url_1_origin whose path "
            "contains call_url_1_decoded_path (equal or beneath, segment by segment, "
            f"{words} as the tool profile declares, never by string prefix). Rules whose urls "
            "field does not cover fetch are in neither list. Comparison only: not a rule match, "
            "exception or decision."
        )
        assert values(out)["call_url_1_inside_prefixes_of"] == ([] if sensitive else ["r"])
    plain = U.compute(request([effect("https://example.com/a")]))
    data = U.compute(request([effect("https://example.com/a;b", semicolon=True)]))
    sentence = "A ; is path data, as the tool profile declares. "
    assert sentence not in note(plain, "call_url_1_decoded_path")
    assert sentence + "Applied here:" in note(data, "call_url_1_decoded_path")


def test_the_flag_notes_count_what_was_found():
    out = U.compute(request([effect("https://example.com/a%2fb/%2E%2e/%5Cc")]))
    facts = {f["name"]: f for f in out["facts"]}
    flag = facts["call_url_1_encoded_separator_or_dot_segment"]
    assert flag["value"] is True
    assert "Found: 2 encoded separator(s), 1 encoded dot segment(s)." in flag["note"]
    assert facts["call_url_1_double_encoded"]["value"] is False
    out = U.compute(request([effect("https://example.com/a/b%2541")]))
    facts = {f["name"]: f for f in out["facts"]}
    assert facts["call_url_1_double_encoded"]["value"] is True
    assert facts["call_url_1_double_encoded"]["note"] == (
        "Whether decoding every percent sequence of the path once leaves a percent sequence, so "
        "that a second decoding would change it again. call_url_1_decoded_path is never decoded "
        "a second time; the host refuses a URL whose comparison lists a second decoding would "
        "change."
    )


def test_facts_are_comparisons_never_a_rule_match_or_a_decision():
    rules = U.compile_policy([{"id": "docs", "urls": {"within": "https://example.com/x/"}}])
    out = U.compute(request([effect("https://example.com/x/a")], rules))
    lists = [f for f in out["facts"] if isinstance(f["value"], list)]
    assert len(lists) == 4
    for fact in lists:
        assert fact["note"].endswith("Comparison only: not a rule match, exception or decision.")
    names = " ".join(f["name"] for f in out["facts"])
    assert not any(word in names for word in ("match", "allow", "deny", "authorized"))


def test_no_readable_path_can_put_a_line_break_or_hidden_character_into_a_fact():
    """Every character of every string value in the vectors is printable, visible text."""
    import unicodedata

    for case in VECTORS["cases"]:
        out = U.compute_or_refusal(case["request"])
        for fact in out.get("facts", []):
            for text in [fact["value"], fact["note"]]:
                if isinstance(text, str):
                    assert text.isprintable(), (case["id"], fact["name"])
                    assert not any(unicodedata.category(ch) in ("Cf", "Zl", "Zp") for ch in text)


def test_no_answer_depends_on_the_running_python_s_unicode_tables():
    """Hosts and shown path text are classified with the Unicode 3.2 table that ships in every
    Python (unicodedata.ucd_3_2_0) and with tables written out in the module, so the Kai and
    AgentArmor copies agree whatever interpreter runs them."""
    import re

    source = SOURCE.read_text(encoding="utf-8")
    assert re.findall(r"unicodedata\.(?!ucd_3_2_0\b)\w+", source) == []
    assert ".isspace()" not in source and ".isprintable()" not in source
    for path in ("/x/%F0%9F%98%80", "/x/%CD%B8", "/x/a%C2%ADb", "/x/%E2%80%AE"):
        with pytest.raises(U.Refusal) as refused:
            U.read_path(path, "decode_all_once")
        assert refused.value.code == "invisible_character"
    assert U.read_path("/x/%C3%A9%E4%B8%AD", "decode_all_once")["decoded_path"] == "/x/\u00e9\u4e2d"


def test_every_level_of_encoding_is_read_until_none_is_left():
    assert U.read_path("/x/a%2541", "decode_all_once")["deeper_segments"] == [["x", "aA"]]
    deep = U.read_path("/x/a%25252541", "decode_all_once")["deeper_segments"]
    assert deep == [["x", "a%2541"], ["x", "a%41"], ["x", "aA"]]
    assert U.read_path("/x/a%41", "decode_all_once")["deeper_segments"] == []
    rules = U.compile_policy([{"id": "deny", "urls": {"within": "https://example.com/admin"}}])
    for levels in range(2, 9):  # a letter of the deny prefix hidden 2..8 levels deep
        raw = "https://example.com/%" + "25" * (levels - 1) + "61dmin/keys"
        out = U.compute_or_refusal(request([effect(raw, decoding="decode_all_once")], rules))
        assert (out["status"], out.get("code")) == ("refused", "ambiguous_double_decoding")
        assert U.compute(request([effect(raw, decoding="decode_all_once")]))["status"] == "ok"


def test_the_same_request_gives_the_same_bytes():
    made = request(
        [effect("https://BÜCHER.example/a/%2e%2e/b", decoding="decode_all_once")],
        U.compile_policy([{"id": "r", "urls": {"within": "https://bücher.example/b"}}]),
    )
    first = json.dumps(U.compute(json.loads(json.dumps(made))), sort_keys=True)
    assert first == json.dumps(U.compute(made), sort_keys=True)
    assert values(U.compute(made))["call_url_1_inside_prefixes_of"] == ["r"]


def fact_line(fact):
    """One environment line in Kai's envelope-v2 fact syntax (Kai docs/architecture.md 4.1):
    `  - <name>: <JSON value> [unit=...; provenance=...; as_of="..."; note="..."]`."""

    def js(value):
        return json.dumps(value, ensure_ascii=False)

    return (
        f"  - {fact['name']}: {js(fact['value'])} [unit={js(fact['unit'])}; "
        f"provenance={fact['provenance']}; as_of={js(fact['as_of'])}; note={js(fact['note'])}]"
    )


def test_integration_policy_file_to_rendered_host_facts_lines():
    """A policy file's rules, one proposed call and its rendered "Host facts:" lines.

    The call sends data to a URL that reads as the allowed upload subtree but leaves it through
    encoded dot segments, on the origin's explicit default port and with an uppercase host.
    """
    policy = [
        {
            "id": "uploads",
            "tier": "allow",
            "description": "Uploads to the team's upload area are fine.",
            "urls": {"within": "https://files.example.com/team/uploads/", "for": ["send"]},
        },
        {
            "id": "no-admin",
            "tier": "hard_deny",
            "description": "Never call the admin interface.",
            "urls": {"within": "https://files.example.com/admin"},
        },
        {
            "id": "partner",
            "tier": "soft_deny",
            "description": "Anything sent to the partner needs my approval.",
            "urls": {"within": "https://partner.example.net", "for": ["send"]},
        },
        {"id": "task", "tier": "allow", "description": "Calls needed for the delegated task."},
        {
            "id": "reads",
            "tier": "allow",
            "description": "Reading the public documentation is fine.",
            "urls": {"within": "https://files.example.com/docs", "for": ["fetch"]},
        },
    ]
    call = {
        "tool": "http_upload",
        "arguments": {
            "url": "https://FILES.example.com:443/team/uploads/%2e%2e/%2E%2E/admin/keys?x=1",
            "body": "...",
        },
    }
    # The tool profile (trusted) says which argument is a URL effect, how the server decodes,
    # whether it compares names case-sensitively and whether it keeps ; as data.
    effects = [
        {
            "argument": "/call/arguments/url",
            "raw": call["arguments"]["url"],
            "operation": "send",
            "decoding": "rfc3986",
            "case_sensitive": True,
            "semicolon_is_data": False,
        }
    ]
    out = U.compute(
        {
            "profile": U.PROFILE,
            "observed_at": AT,
            "effects": effects,
            "url_rules": U.compile_policy(policy),
        }
    )
    rendered = "\n".join(["Host facts:"] + [fact_line(f) for f in out["facts"]])
    lines = rendered.split("\n")
    assert len(lines) == 9 == len(rendered.splitlines())
    start = [line.split(" [unit=null; provenance=host_computed; ")[0] for line in lines]
    assert start == [
        "Host facts:",
        '  - call_url_1_origin: "https://files.example.com:443"',
        '  - call_url_1_same_origin_as: ["uploads", "no-admin"]',
        '  - call_url_1_different_origin_from: ["partner"]',
        '  - call_url_1_decoded_path: "/admin/keys"',
        "  - call_url_1_encoded_separator_or_dot_segment: true",
        "  - call_url_1_double_encoded: false",
        '  - call_url_1_inside_prefixes_of: ["no-admin"]',
        '  - call_url_1_outside_prefixes_of: ["uploads", "partner"]',
    ]
    assert lines[1] == (
        '  - call_url_1_origin: "https://files.example.com:443" [unit=null; '
        'provenance=host_computed; as_of="2026-11-22T12:00:00Z"; note="Host reading of call '
        "argument /call/arguments/url for send as scheme://host:port, without network access: "
        "scheme and host lowercased, userinfo ignored, non-ASCII host as IDNA A-labels, the "
        "scheme's default port when none is written. Applied here: uppercase host letters "
        'lowercased; explicit port 443 kept; it is the default for https."]'
    )
    assert lines[7] == (
        '  - call_url_1_inside_prefixes_of: ["no-admin"] [unit=null; provenance=host_computed; '
        'as_of="2026-11-22T12:00:00Z"; note="Policy rules with a declared URL prefix of origin '
        "call_url_1_origin whose path contains call_url_1_decoded_path (equal or beneath, segment "
        "by segment, case-sensitive as the tool profile declares, never by string prefix). Rules "
        "whose urls field does not cover send are in neither list. Comparison only: not a rule "
        'match, exception or decision."]'
    )
    # The "reads" rule covers fetch only, so a send is in none of its lists; "task" has no urls.
    assert "reads" not in rendered and '"task"' not in rendered
    # The plain twin of the same call: only host facts tell the two apart.
    plain = dict(effects[0], raw="https://files.example.com/team/uploads/keys")
    told = values(U.compute(request([plain], U.compile_policy(policy))))
    assert told["call_url_1_inside_prefixes_of"] == ["uploads"]
    assert told["call_url_1_encoded_separator_or_dot_segment"] is False
    # A URL the host cannot read gives no section at all: the request is refused.
    odd = dict(effects[0], raw="https://files.example.com/team/uploads/..;/admin/keys")
    refused = U.compute_or_refusal(request([odd], U.compile_policy(policy)))
    assert (refused["status"], refused["code"]) == ("refused", "path_parameter")
    assert "facts" not in refused
