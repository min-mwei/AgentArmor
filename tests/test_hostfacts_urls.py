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


def effect(raw, operation="fetch", decoding="rfc3986", argument="/call/arguments/url"):
    return {"argument": argument, "raw": raw, "operation": operation, "decoding": decoding}


def values(out):
    return {f["name"]: f["value"] for f in out["facts"]}


def test_manifest_pins_the_reference_and_the_fixture():
    assert sha(SOURCE) == MANIFEST["reference_python_sha256"]
    assert sha(FIXTURE / "vectors.json") == MANIFEST["vectors_sha256"]
    assert sha(FIXTURE / "make_vectors.py") == MANIFEST["builder_sha256"]
    assert len(VECTORS["cases"]) == MANIFEST["cases"]
    assert len(VECTORS["policy_cases"]) == MANIFEST["policy_cases"]
    assert VECTORS["profile"] == MANIFEST["profile"] == U.PROFILE


def test_the_new_kind_leaves_the_path_and_measure_references_as_pinned():
    # A new kind is a new profile beside the others: neither existing reference file changes.
    for kind, name in (("paths", "paths"), ("measures", "measures")):
        manifest = ROOT / f"tests/fixtures/armor-hostfacts-{kind}-v1/MANIFEST.json"
        pinned = json.loads(manifest.read_text(encoding="utf-8"))["reference_python_sha256"]
        assert sha(ROOT / f"src/hostfacts/{name}.py") == pinned


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_hand_written_vector(case):
    out = U.compute_or_refusal(case["request"])
    expect = case["expect"]
    if expect["status"] == "refused":
        assert (out["status"], out.get("code")) == ("refused", expect["code"])
        return
    assert out["status"] == "ok", out
    assert [[f["name"], f["value"]] for f in out["facts"]] == expect["values"]
    assert out["unusable"] == expect["unusable"]
    for fact in out["facts"]:
        assert set(fact) == {"name", "value", "unit", "provenance", "as_of", "note"}
        assert (fact["unit"], fact["provenance"]) == (None, "host_computed")
        assert fact["as_of"] == case["request"]["observed_at"] and fact["note"].endswith(".")


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


def test_every_unusable_code_and_refusal_code_has_a_vector():
    seen = {
        value
        for case in VECTORS["cases"]
        if case["expect"]["status"] == "ok"
        for name, value in case["expect"]["values"]
        if name.endswith("_unusable")
    }
    assert seen == set(U.UNUSABLE_CODES)
    refused = {c["expect"]["code"] for c in VECTORS["cases"] if c["expect"]["status"] == "refused"}
    assert refused == set(U.REFUSAL_CODES)
    errors = {
        c["expect"]["code"] for c in VECTORS["policy_cases"] if c["expect"]["status"] == "error"
    }
    assert errors == set(U.POLICY_ERRORS)


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
            "HTTPS://user@Example.COM.:443/",
            [
                "uppercase scheme letters lowercased",
                "userinfo before @ is not part of the origin and was ignored",
                "uppercase host letters lowercased",
                "one trailing dot removed from the host",
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
            "https://[0:0:0:0:0:0:0:1]:8443/",
            ["IPv6 literal written in its compressed lowercase form", "explicit port 8443 kept"],
        ),
    ],
)
def test_the_origin_note_names_every_normalization_step_that_changed_something(raw, steps):
    out = U.compute(request([effect(raw)]))
    note = out["facts"][0]["note"]
    assert out["facts"][0]["name"] == "call_url_1_origin"
    assert note.endswith("Applied here: " + "; ".join(steps) + ".")
    assert "without network access" in note


def test_the_path_note_states_the_one_decoding_profile_and_never_the_other():
    for decoding, other in (("rfc3986", "decode_all_once"), ("decode_all_once", "rfc3986")):
        out = U.compute(request([effect("https://example.com/../a%2Fb", decoding=decoding)]))
        note = next(f["note"] for f in out["facts"] if f["name"] == "call_url_1_decoded_path")
        assert f"decoding profile {decoding}:" in note and other not in note
        assert note.endswith("Applied here: a .. segment at the root was dropped.")
    out = U.compute(request([effect("https://example.com")]))
    note = next(f["note"] for f in out["facts"] if f["name"] == "call_url_1_decoded_path")
    assert note.endswith("Applied here: empty path read as /.")


def test_the_flag_notes_count_what_was_found():
    out = U.compute(request([effect("https://example.com/a%2fb/%2E%2e/%5Cc/%252F")]))
    facts = {f["name"]: f for f in out["facts"]}
    flag = facts["call_url_1_encoded_separator_or_dot_segment"]
    assert flag["value"] is True
    assert "Found: 2 encoded separator(s), 1 encoded dot segment(s)." in flag["note"]
    assert facts["call_url_1_double_encoded"]["value"] is True


def test_an_unusable_url_is_one_fact_that_says_so_and_never_a_guess():
    rules = U.compile_policy([{"id": "docs", "urls": {"within": "https://example.com/"}}])
    out = U.compute(request([effect("https://example.com\\@evil.example/")], rules))
    assert out["status"] == "ok" and out["unusable"] == [1] and len(out["facts"]) == 1
    fact = out["facts"][0]
    assert (fact["name"], fact["value"]) == ("call_url_1_unusable", "backslash")
    assert "none may be inferred from its raw text" in fact["note"]
    assert "evil" not in fact["note"]  # the note never repeats caller text


def test_facts_are_comparisons_never_a_rule_match_or_a_decision():
    rules = U.compile_policy([{"id": "docs", "urls": {"within": "https://example.com/x/"}}])
    out = U.compute(request([effect("https://example.com/x/a")], rules))
    lists = [f for f in out["facts"] if isinstance(f["value"], list)]
    assert len(lists) == 4
    for fact in lists:
        assert fact["note"].endswith("Comparison only: not a rule match, exception or decision.")
    names = " ".join(f["name"] for f in out["facts"])
    assert not any(word in names for word in ("match", "allow", "deny", "authorized"))


def test_the_same_request_gives_the_same_bytes():
    made = request(
        [effect("https://BÜCHER.example./a/%2e%2e/b", decoding="decode_all_once")],
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
    # The tool profile (trusted) says which argument is a URL effect and how the server decodes.
    effects = [
        {
            "argument": "/call/arguments/url",
            "raw": call["arguments"]["url"],
            "operation": "send",
            "decoding": "rfc3986",
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
    assert out["unusable"] == []
    rendered = "\n".join(["Host facts:"] + [fact_line(f) for f in out["facts"]])
    lines = rendered.split("\n")
    assert len(lines) == 9 and all("\n" not in f["note"] for f in out["facts"])
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
        "scheme and host lowercased, userinfo ignored, one trailing host dot removed, non-ASCII "
        "host as IDNA A-labels, the scheme's default port when none is written. Applied here: "
        'uppercase host letters lowercased; explicit port 443 kept; it is the default for https."]'
    )
    assert lines[7] == (
        '  - call_url_1_inside_prefixes_of: ["no-admin"] [unit=null; provenance=host_computed; '
        'as_of="2026-11-22T12:00:00Z"; note="Policy rules with a declared URL prefix of origin '
        "call_url_1_origin whose path contains call_url_1_decoded_path (equal or beneath, segment "
        "by segment, case-sensitive, never by string prefix). Rules whose urls field does not "
        "cover send are in neither list. Comparison only: not a rule match, exception or "
        'decision."]'
    )
    # The "reads" rule covers fetch only, so a send is in none of its lists; "task" has no urls.
    assert "reads" not in rendered and '"task"' not in rendered
    # The same call under the other profile, and its plain twin: only host facts tell them apart.
    plain = dict(effects[0], raw="https://files.example.com/team/uploads/keys")
    told = values(U.compute(request([plain], U.compile_policy(policy))))
    assert told["call_url_1_inside_prefixes_of"] == ["uploads"]
    assert told["call_url_1_encoded_separator_or_dot_segment"] is False
