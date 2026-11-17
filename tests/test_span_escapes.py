"""The escape-remainder reading of a decoded evidence interval (agentarmor_monitor.spans.escaped,
ESCAPE): the inverse of the training projection's serializer-escape-remainder-v1 rule, read only
from the escapes the state's own source map proves, and named by the package before it is used.
Synthetic states and maps; nothing here says a span is right."""

import hashlib

import pytest
from agentarmor_monitor import kai, spans
from agentarmor_vllm.package import LoraPackage
from summarizer.sourcemap import SCHEMA, SourceMap

WITH = {"rule": spans.WRAPPER, "wrapper_sources": ["quote"], "escapes": spans.ESCAPE}
WITHOUT = {"rule": spans.WRAPPER, "wrapper_sources": ["quote"]}
STATE = 'X: "alpha\\nbeta" end'  # a session value "alpha<newline>beta" written as a JSON string


def session(at, source, exact=True, kind="session"):
    piece = {"at": at, "kind": kind, "field": "text", "source": source}
    if kind == "session":
        piece |= {"session": "s1", "seq": 1, "trust": "lower", "type": "tool_result"}
    return piece | ({"exact": True} if exact else {})


def mapped(state=STATE, segments=None):
    told = {
        "schema": SCHEMA,
        "sha256": hashlib.sha256(state.encode()).hexdigest(),
        "chars": len(state),
        "cutoff": {"seq": 2, "session": "s1"},
        "sessions": {},
    }
    told["segments"] = segments or [
        {"at": [0, 3], "kind": "synthesized"},
        {"at": [3, 4], "kind": "synthesized", "role": "quote"},
        session([4, 9], [0, 5]),
        session([9, 11], [5, 6], exact=False),  # the newline, spelled as two characters
        session([11, 15], [6, 10]),
        {"at": [15, 16], "kind": "synthesized", "role": "quote"},
        {"at": [16, 20], "kind": "synthesized"},
    ]
    return SourceMap.read(told)


def test_the_map_proves_the_escape_and_nothing_else():
    assert spans.escapes(mapped(), STATE) == [[9, 11, 4, 15]]
    with pytest.raises(ValueError):
        spans.escapes(mapped(), STATE + "!")


def test_an_edge_inside_an_escape_reads_back_as_the_value():
    read = spans.projected([[10, 15], [4, 10], [4, 9]], STATE, mapped(), WITH)
    assert read["escapes"] == spans.ESCAPE
    first, second, plain = read["spans"]
    assert (first["rule"], first["value"]) == (
        spans.ESCAPE,
        [11, 15],
    )  # the hull began at the escape's "n"
    assert (second["rule"], second["value"]) == (
        spans.ESCAPE,
        [4, 9],
    )  # the hull ended at its backslash
    assert (plain["rule"], plain["value"]) == (spans.EXACT, [4, 9])


def test_negative_controls():
    # the package did not name the rule: no escape is taken, the interval stays as decoded
    told = spans.projected([[10, 15]], STATE, mapped(), WITHOUT)["spans"][0]
    assert (told["rule"], told["value"]) == (spans.EXACT, [10, 15])
    # an escape rule this code does not have is named and not applied
    assert (
        spans.projected([[10, 15]], STATE, mapped(), WITHOUT | {"escapes": "other-rule-v9"})[
            "status"
        ]
        == "unknown_rule"
    )
    # a backslash pair the map says is exact content (a content backslash) is no escape
    content = mapped(
        segments=[
            {"at": [0, 3], "kind": "synthesized"},
            {"at": [3, 4], "kind": "synthesized", "role": "quote"},
            session([4, 15], [0, 11]),
            {"at": [15, 16], "kind": "synthesized", "role": "quote"},
            {"at": [16, 20], "kind": "synthesized"},
        ]
    )
    assert spans.escapes(content, STATE) == []
    assert spans.projected([[10, 15]], STATE, content, WITH)["spans"][0]["value"] == [10, 15]
    # the call's own JSON is never a session value's escape
    call = mapped(
        segments=[
            {"at": [0, 4], "kind": "synthesized"},
            session([4, 9], [0, 5], kind="call"),
            session([9, 11], [5, 6], exact=False, kind="call"),
            session([11, 15], [6, 10], kind="call"),
            {"at": [15, 20], "kind": "synthesized"},
        ]
    )
    assert spans.escapes(call, STATE) == []
    # an escape at one edge and a mark at the other is no single rule's reading
    both = spans.projected([[10, 16]], STATE, mapped(), WITH)["spans"][0]
    assert (both["rule"], both["why"]) == (None, "escape_and_mark")
    # a shifted interval is not the value
    assert (
        spans.projected([[11, 15]], STATE, mapped(), WITH)["spans"][0]["value"]
        == [11, 15]
        != [12, 15]
    )


def test_the_package_and_the_client_accept_the_named_rule_and_refuse_malformed_ones():
    told = {
        "readout": "joint_pairs_memory_v1",
        "impl_sha256": "a" * 64,
        "threshold": 0.0,
        "max_spans": 32,
        "targets": WITH,
    }
    assert LoraPackage._check_evidence(dict(told))["targets"] == WITH
    assert LoraPackage._check_evidence(told | {"targets": WITHOUT})["targets"] == WITHOUT
    for bad in (WITH | {"escapes": 1}, WITH | {"escapes": "Not A Rule"}, WITH | {"other": "x"}):
        with pytest.raises(ValueError):
            LoraPackage._check_evidence(told | {"targets": bad})
    from test_span_readings import NEXT, at, hop
    from test_span_readings import STATE as SERVED

    reply, _, _ = hop([at(f'"{NEXT}"')])
    named = reply | {"evidence": reply["evidence"] | {"targets": WITH}}
    assert kai.evidence(named, kai.XPIA, SERVED).get("why") != "targets_malformed"
    bad = reply | {"evidence": reply["evidence"] | {"targets": WITH | {"escapes": 3}}}
    assert kai.evidence(bad, kai.XPIA, SERVED)["why"] == "targets_malformed"
