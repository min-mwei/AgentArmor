"""Where each character of a rendered summary came from (summarizer.sourcemap)."""

import hashlib
import json
import random

import pytest
from conftest import summarize
from sessions import Script, attack, benign

from summarizer.facts import visible
from summarizer.sourcemap import (
    DERIVED,
    SESSION,
    SYNTHESIZED,
    UNMAPPED,
    SourceMap,
    from_utf16,
    map_of,
    order,
    spelled,
    tracing,
    utf16,
)
from summarizer.text import digest, quote

ODD = 'e\u0301 caf\u00e9 \U0001f600 "q" back\\slash\ttab\nline \u200bhidden <|fim_prefix|> end'


def mapped(summary, budget=6000, focus=None):
    text = digest(summary, budget, focus=focus)["text"]
    found = map_of(lambda: digest(summary, budget, focus=focus)["text"], plain=text)
    assert found is not None
    return text, found


def field_of(summary, piece):
    """The source field a resolved piece names, read from the summary."""
    if piece["type"] == "session":
        name, _, rest = piece["field"].partition("[")
        if not rest:
            return summary["session"][name]
        index, _, key = rest.partition("]")
        return summary["session"][name][int(index)][key[1:]]
    if piece["type"] == "observable":
        return summary["iocs"][int(piece["field"][5:].partition("]")[0])]["value"]
    entry = next(
        e
        for e in summary["entries"]
        if (e["seq"], e["type"]) == (piece["seq"], piece["type"])
        and (piece.get("ref") is None or e.get("ref") == piece["ref"])
    )
    name, _, rest = piece["field"].partition("[")
    if not rest:
        return entry[name]
    index, _, key = rest.partition("]")
    held = entry[name][int(index)]
    return held[key[1:]] if key else held


def odd_session():
    script = Script("claude_code", "odd-1").start()
    script.prompt(f"Tidy the notes. {ODD}")
    script.prompt("Long request: " + "word " * 400)
    script.prompt("yes")
    script.tool("Bash", {"command": "git status"}, "clean")
    script.prompt("yes")
    script.tool("Bash", {"command": "git status"}, f"nothing to commit {ODD}")
    script.tool("Read", {"file_path": "/srv/work/proj/notes.md"}, "x" * 900 + " the end")
    return script.stop(f"Done. {ODD}").events


@pytest.mark.parametrize("events", [benign, attack, odd_session])
def test_every_character_is_in_exactly_one_segment_and_a_trace_changes_nothing(runtime, events):
    summary = summarize(events(), runtime)
    for budget in (700, 1500, 3000, 20000):
        text, found = mapped(summary, budget)
        assert found.chars == len(text)
        cursor = 0
        for segment in found.segments:
            assert segment["at"][0] == cursor and segment["at"][1] > cursor
            cursor = segment["at"][1]
        assert cursor == len(text)
        assert {s["kind"] for s in found.segments} <= {SESSION, DERIVED, SYNTHESIZED}


@pytest.mark.parametrize("events", [benign, attack, odd_session])
def test_a_session_segment_is_the_rendered_form_of_its_source_interval(runtime, events):
    summary = summarize(events(), runtime)
    text, found = mapped(summary, 20000)
    seen = 0
    for segment in found.segments:
        if segment["kind"] not in (SESSION, DERIVED):
            continue
        seen += 1
        low, high = segment["source"]
        source = field_of(summary, segment)[low:high]
        shown = text[segment["at"][0] : segment["at"][1]]
        whole = json.dumps(visible(source), ensure_ascii=False)[1:-1]
        # All of the interval, or, where a cut fell inside one spelled-out character, its start.
        assert shown == whole or (not segment["exact"] and whole.startswith(shown) and shown)
        assert segment["session"] == summary["session"]["session_id"]
        if segment["field"] in ("tool", "actor", "agent"):  # a bare word: all of it, no marks
            assert shown == source == field_of(summary, segment)
    assert seen


def test_what_summarizer_derived_is_told_apart_from_what_the_session_said(runtime):
    """An indicator's basis, a note and an observable's value are the summary's own fields:
    mapped to those, with the event they are about and its trust, and with no interval in
    the event's text. The working directory and a tool's name are the session's."""
    summary = summarize(attack(), runtime)
    focus = next(
        e["call_id"] for e in summary["entries"] if e.get("indicators") and e.get("call_id")
    )
    text, found = mapped(summary, 20000, focus=focus)
    kinds = {}
    for segment in found.segments:
        if segment["kind"] in (SESSION, DERIVED):
            head = segment["field"].partition("[")[0]
            kinds.setdefault((segment["kind"], segment["type"], head), []).append(segment)
    assert (SESSION, "session", "cwd") in kinds and (SESSION, "action", "tool") in kinds
    assert (DERIVED, "action", "indicators") in kinds and (DERIVED, "observable", "iocs") in kinds
    for (kind, _, head), segments in kinds.items():
        assert (kind == DERIVED) == (head in ("indicators", "notes", "iocs"))
        for segment in segments:
            assert "ref" not in segment or kind == SESSION
    (value,) = [s for s in kinds[(DERIVED, "observable", "iocs")] if s["source"][0] == 0][:1]
    assert value["seq"] is None and value["named"] and all(len(m) == 2 for m in value["named"])
    session = summary["session"]["session_id"]
    assert found.locate(session, None, "cwd", 0, 1)  # found as a session field, by identity
    assert not found.locate(session, None, value["field"], 0, 1)  # not a session field
    assert found.locate(session, None, value["field"], 0, 1, kind=DERIVED)
    assert UNMAPPED not in {s["kind"] for s in found.segments}


def test_order_is_by_event_number_in_the_calls_session_and_by_time_in_another():
    cut = {"until": 100.0, "unknown": ["unlisted_sightings"], "tied": False}
    found = SourceMap("x" * 10, [], cutoff={"session": "now", "seq": 7}, sessions={"then": cut})

    def piece(**about):
        return {"kind": SESSION, "session": "then", "seq": 3, "ts": 50.0} | about

    assert order({"kind": SYNTHESIZED}, found) == order({"kind": "policy"}, found) == "before"
    assert order(piece(session="now", seq=7), found) == "before"
    assert order(piece(session="now", seq=8), found) == "after"
    assert order(piece(session="now", seq=None), found) == "before"
    assert order(piece(), found) == order(piece(kind=DERIVED), found) == "before"
    assert order(piece(ts=100.5), found) == "after"
    assert order(piece(ts=100.0), found) == "unknown"  # at the very time of the call
    assert order(piece(ts=None), found) == order(piece(ts=True), found) == "unknown"
    assert order(piece(seq=None, ts=None), found) == "before"  # the cut session's own account
    assert order(piece(session="elsewhere"), found) == "unknown"  # a session with no cut
    for unknown in ({"until": 0}, {"until": None}, {"until": 100.0, "unknown": ["call_time"]}):
        found.sessions["then"] = unknown
        assert order(piece(), found) == "unknown"
    again = SourceMap.read(found.told())
    assert again.sessions == found.sessions and order(piece(), again) == "unknown"


def test_marks_titles_the_cut_sign_and_quotation_marks_are_no_source(runtime):
    summary = summarize(odd_session(), runtime)
    text, found = mapped(summary, 20000)
    for segment in found.segments:
        shown = text[segment["at"][0] : segment["at"][1]]
        if segment["kind"] == SESSION:
            assert "…" not in shown or "…" in field_of(summary, segment)
    title = text.index("REQUESTS ON THE USER CHANNEL")
    assert [p["kind"] for p in found.resolve(title, title + 8)] == [SYNTHESIZED]
    cut = text.index("…")
    assert [p["kind"] for p in found.resolve(cut, cut + 1)] == [SYNTHESIZED]
    assert all("source" not in p for p in found.resolve(cut, cut + 1))


def test_a_repeated_phrase_is_resolved_by_where_it_stands_never_by_what_it_says(runtime):
    summary = summarize(odd_session(), runtime)
    text, found = mapped(summary, 20000)
    places = [i for i in range(len(text)) if text.startswith('"yes"', i)]
    assert len(places) == 2
    seqs = []
    for place in places:
        (piece,) = found.resolve(place + 1, place + 4)
        assert (piece["kind"], piece["field"], piece["source"]) == (SESSION, "text", [0, 3])
        assert piece["trust"] == "user"
        seqs.append(piece["seq"])
    assert seqs[0] < seqs[1]
    # And back: each prompt is found at its own place, and only there.
    for seq, place in zip(seqs, places, strict=True):
        assert found.locate(summary["session"]["session_id"], seq, "text", 0, 3) == [
            [place + 1, place + 4]
        ]


def test_escapes_and_spelled_out_characters_resolve_to_whole_source_characters(runtime):
    summary = summarize(odd_session(), runtime)
    text, found = mapped(summary, 20000)
    prompt = next(e for e in summary["entries"] if e["type"] == "prompt" and "Tidy" in e["text"])
    session = summary["session"]["session_id"]
    for needle in (
        "\u0301",
        "\u00e9",
        "\U0001f600",
        '"',
        "\\",
        "\t",
        "\n",
        "\u200b",
        "<|fim_prefix|>",
    ):
        start = prompt["text"].index(needle)
        (at,) = found.locate(session, prompt["seq"], "text", start, start + len(needle))
        assert text[at[0] : at[1]] == json.dumps(visible(needle), ensure_ascii=False)[1:-1]
        pieces = found.resolve(at[0], at[1])
        assert pieces[0]["source"][0] == start and pieces[-1]["source"][1] == start + len(needle)
    # One character inside a spelled-out character is still that whole source character.
    hidden = prompt["text"].index("\u200b")
    (at,) = found.locate(session, prompt["seq"], "text", hidden, hidden + 1)
    assert at[1] - at[0] == len("\\\\u{200b}")
    (piece,) = found.resolve(at[0] + 2, at[0] + 3)
    assert piece["source"] == [hidden, hidden + 1]


def test_what_a_cut_left_out_cannot_be_located_and_nothing_near_it_is_offered(runtime):
    summary = summarize(odd_session(), runtime)
    _, found = mapped(summary, 20000)
    content = next(e for e in summary["entries"] if e["type"] == "content" and "xxx" in e["text"])
    session = summary["session"]["session_id"]
    seq, ref = content["seq"], content["ref"]
    assert found.locate(session, seq, "text", 0, 5, ref=ref) != []
    assert found.locate(session, seq, "text", 0, 5) == []  # a piece is named with its place
    whole = len(content["text"])
    shown = found.locate(session, seq, "text", 0, whole, ref=ref)
    assert shown == [] or shown[0][1] - shown[0][0] == whole  # all of it or none of it
    assert found.locate(session, seq, "text", whole + 5, whole + 9, ref=ref) == []
    assert found.locate("another-session", seq, "text", 0, 5, ref=ref) == []
    assert found.locate(session, seq, "origin", 0, 500) == []


def test_a_piece_of_content_says_where_in_its_event_it_sits(runtime):
    summary = summarize(attack(), runtime)
    _, found = mapped(summary, 20000)
    pieces = [s for s in found.segments if s["kind"] == SESSION and s.get("ref")]
    assert pieces
    for piece in pieces:
        assert set(piece["ref"]) == {"field", "start", "end"} and piece["type"] == "content"
        assert piece["trust"] not in ("user", "agent")


def test_a_map_holds_no_text_and_names_the_exact_text_it_is_of(runtime):
    summary = summarize(attack(), runtime)
    text, found = mapped(summary, 3000)
    assert found.sha256 == hashlib.sha256(text.encode()).hexdigest()
    said = (str(e.get("text", "")) for e in summary["entries"])
    words = {w for text in said for w in text.split() if len(w) > 6} - {"content"}  # a type's name
    held = json.dumps(found.segments)
    assert words and not any(word in held for word in words)
    other, again = mapped(summary, 1200)
    assert other != text and again.sha256 != found.sha256  # a new rendering is a new map


def test_session_text_that_holds_the_stand_in_characters_has_no_map(runtime):
    script = Script("claude_code", "pua-1").start()
    script.prompt("Rename \ue000\ue100\ue002 in the font table.")
    summary = summarize(script.events, runtime)
    assert map_of(lambda: digest(summary, 3000)["text"]) is None


def test_other_private_use_characters_do_not_cost_a_session_its_map(runtime):
    """Only the characters stand-ins are made of are in the way; a font's or a vendor's own
    private characters beside them are ordinary session text."""
    script = Script("claude_code", "pua-2").start()
    script.prompt("Keep the logo \uf8ff and the glyph \uf100 in the header.")
    summary = summarize(script.events, runtime)
    text, found = mapped(summary, 3000)
    at = text.index("\uf8ff")
    (piece,) = found.resolve(at, at + 1)
    assert piece["kind"] == SESSION and piece["trust"] == "user"
    low, high = piece["source"]
    assert field_of(summary, piece)[low:high] == "\uf8ff"


def test_a_rendering_that_differs_under_trace_has_no_map():
    calls = []

    def unstable():
        calls.append(1)
        return f"line {len(calls)} {quote('some session words', 80)}"

    assert map_of(unstable) is None


def test_a_quote_without_an_origin_and_a_bare_literal_are_unmapped_not_synthesized():
    def render():
        return f'note {quote("told by nobody", 80)} and "a bare literal" and words'

    found = map_of(render)
    kinds = [s["kind"] for s in found.segments]
    assert kinds == [SYNTHESIZED, UNMAPPED, SYNTHESIZED, UNMAPPED, SYNTHESIZED]
    assert found.resolve(6, 9)[0]["kind"] == UNMAPPED


def test_a_false_origin_is_not_believed(runtime):
    summary = summarize(benign(), runtime)
    entry = summary["entries"][0]
    stranger = dict(entry)

    def render():
        with_other_text = quote("not this entry's text", 80, (entry, "text"))
        with_a_copy = quote(stranger["text"], 80, (stranger, "text"))
        return f"{with_other_text} {with_a_copy}"

    with tracing() as trace:
        trace.entering(summary)
        render()
    assert [origin for *_, origin in trace.quotes] == [None, None]


def test_spelling_follows_quote_on_random_text():
    rng = random.Random(7)
    alphabet = 'ab "\\\n\t\u200b\u0301\u00e9\U0001f600\x00\x7f…{}'
    for _ in range(400):
        source = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 40)))
        for limit in (1, 2, 5, 17, 80):
            body = "".join(text for text, _ in spelled(source, limit))
            assert '"' + body + '"' == quote(source, limit)


def test_a_frame_places_the_rendering_among_other_pieces(runtime):
    summary = summarize(benign(), runtime)

    def frame(text):
        return [("policy", {"rule": "r1"}, "rule one\n"), (None, None, text), ("call", {}, "\nx")]

    found = map_of(lambda: digest(summary, 3000)["text"], frame=frame)
    assert found.segments[0] == {"at": [0, 9], "kind": "policy", "rule": "r1"}
    assert found.segments[-1]["kind"] == "call" and found.segments[-1]["at"][1] == found.chars
    with pytest.raises(ValueError):
        found.resolve(0, found.chars + 1)
    with pytest.raises(ValueError):
        found.resolve(3, 3)


def test_a_span_over_a_redaction_is_an_interval_of_the_marker_and_no_secret_is_held(runtime):
    from sessions import FAKE_KEY_ID, FAKE_SECRET

    summary = summarize(attack(), runtime)
    text, found = mapped(summary, 20000)
    marker = "[REDACTED:"
    place = text.index(marker)
    (piece,) = found.resolve(place, place + len(marker))
    assert piece["kind"] == SESSION
    low, high = piece["source"]
    assert field_of(summary, piece)[low:high] == marker  # the marker, as the summary holds it
    held = json.dumps(found.told())
    assert FAKE_KEY_ID not in held and FAKE_SECRET not in held and FAKE_SECRET not in text


def test_a_map_told_and_read_back_answers_alike(runtime):
    from summarizer.sourcemap import SourceMap

    summary = summarize(odd_session(), runtime)
    _, found = mapped(summary, 20000)
    again = SourceMap.read(json.loads(json.dumps(found.told())))
    assert again.resolve(40, 400) == found.resolve(40, 400) and again.sha256 == found.sha256
    with pytest.raises(ValueError):
        SourceMap.read({"schema": "something-else"})


def test_intervals_convert_to_utf16_units_and_back_and_half_a_character_is_no_place():
    text = "a\U0001f600b e\u0301 \U0001f468\u200d\U0001f469 z"  # an emoji, a combining mark, a joined pair
    assert len(text) == 12 and len(text.encode("utf-16-le")) // 2 == 15
    spans = [[0, 1], [1, 2], [2, 3], [4, 6], [7, 10], [11, 12], [0, 12], [5, 5]]
    units = utf16(text, spans)
    assert units == [[0, 1], [1, 3], [3, 4], [5, 7], [8, 13], [14, 15], [0, 15], [6, 6]]
    encoded = text.encode("utf-16-le")
    for (start, end), (low, high) in zip(spans, units, strict=True):
        assert encoded[2 * low : 2 * high].decode("utf-16-le") == text[start:end]
    assert from_utf16(text, units) == spans
    for bad in ([[2, 3]], [[0, 16]], [[9, 13]], [[-1, 1]], [[3, 1]], [[0.0, 1]]):
        with pytest.raises(ValueError):
            from_utf16(text, bad)
    for bad in ([[0, 13]], [[3, 1]], [[-1, 2]], [["0", 1]]):
        with pytest.raises(ValueError):
            utf16(text, bad)
    assert utf16("", []) == [] and utf16("plain", [[1, 4]]) == [[1, 4]]
