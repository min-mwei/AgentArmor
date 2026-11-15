"""Where each character of a rendered summary came from (summarizer.sourcemap)."""

import hashlib
import json
import random

import pytest
from conftest import summarize
from sessions import Script, attack, benign

from summarizer.facts import visible
from summarizer.sourcemap import SESSION, SYNTHESIZED, UNMAPPED, map_of, spelled, tracing
from summarizer.text import digest, quote

ODD = 'e\u0301 caf\u00e9 \U0001f600 "q" back\\slash\ttab\nline \u200bhidden <|fim_prefix|> end'


def mapped(summary, budget=6000, focus=None):
    text = digest(summary, budget, focus=focus)["text"]
    found = map_of(lambda: digest(summary, budget, focus=focus)["text"], plain=text)
    assert found is not None
    return text, found


def field_of(summary, piece):
    """The source field a resolved piece names, read from the summary."""
    entry = next(
        e
        for e in summary["entries"]
        if (e["seq"], e["type"]) == (piece["seq"], piece["type"])
        and (piece.get("ref") is None or e.get("ref") == piece["ref"])
    )
    name, _, index = piece["field"].partition("[")
    return entry[name][int(index[:-1])] if index else entry[name]


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
        assert {s["kind"] for s in found.segments} <= {SESSION, SYNTHESIZED, UNMAPPED}


@pytest.mark.parametrize("events", [benign, attack, odd_session])
def test_a_session_segment_is_the_rendered_form_of_its_source_interval(runtime, events):
    summary = summarize(events(), runtime)
    text, found = mapped(summary, 20000)
    seen = 0
    for segment in found.segments:
        if segment["kind"] != SESSION:
            continue
        seen += 1
        low, high = segment["source"]
        source = field_of(summary, segment)[low:high]
        shown = text[segment["at"][0] : segment["at"][1]]
        whole = json.dumps(visible(source), ensure_ascii=False)[1:-1]
        # All of the interval, or, where a cut fell inside one spelled-out character, its start.
        assert shown == whole or (not segment["exact"] and whole.startswith(shown) and shown)
        assert segment["session"] == summary["session"]["session_id"]
    assert seen


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
