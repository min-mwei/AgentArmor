"""Evidence bound to a root's own steps, as annotations of the summary's events (root_spans.py)."""

import copy
from pathlib import Path

import campaign_contract as campaigns
import pytest
import root_spans
import span_targets
from common import read_jsonl

from summarizer.sourcemap import map_of
from summarizer.text import digest

STARTER = (
    Path(__file__).resolve().parents[3] / "training/summarizer/data/starter/campaigns-v2.jsonl"
)


@pytest.fixture(scope="module")
def played():
    from conftest import HashEmbedder, Runtime

    root = next(r for r in read_jsonl(STARTER) if r["id"] == "planted-script-run-later")
    laid = campaigns.layout(root)
    summaries, _ = campaigns.replay(laid, Runtime(HashEmbedder(), strict=True))
    return root, laid, summaries


def field(root, session, step, name):
    steps = next(s["steps"] for s in root["sessions"] if s["id"] == session)
    return next(s[name] for s in steps if s["id"] == step)


def entry_text(summaries, session, note):
    entries = summaries[session]["entries"]
    found = next(
        e for e in entries if e["seq"] == note["seq"] and e.get("ref", {}) == note.get("ref", {})
    )
    return found["text"][note["start"] : note["end"]]


def test_a_prompt_a_command_and_a_result_are_found_by_event_never_by_search(played):
    root, laid, summaries = played
    prompt = field(root, "a", "ask", "prompt")
    note, why = root_spans.annotation(laid, summaries, "a", "ask", "prompt", 0, len(prompt))
    assert why is None and note["field"] == "text" and "ref" not in note
    assert note["session"] == summaries["a"]["session"]["session_id"]
    assert entry_text(summaries, "a", note) == prompt

    command = field(root, "b", "go", "run")
    start = command.index("scripts")
    note, why = root_spans.annotation(laid, summaries, "b", "go", "run", start, len(command))
    assert why is None and entry_text(summaries, "b", note) == command[start:]

    output = field(root, "a", "readme", "output")
    start = output.index("Build notes")
    end = start + len("Build notes follow.")
    note, why = root_spans.annotation(laid, summaries, "a", "readme", "output", start, end)
    assert why is None and note["ref"]["end"] - note["ref"]["start"] > 0
    assert entry_text(summaries, "a", note) == "Build notes follow."


def test_the_annotation_is_where_the_state_shows_it(played):
    root, laid, summaries = played
    command = field(root, "b", "go", "run")
    note, _ = root_spans.annotation(laid, summaries, "b", "go", "run", 0, len(command))
    render = lambda: digest(summaries["b"], 6000)["text"]  # noqa: E731
    told = span_targets.project(map_of(render), [note | {"question": "decision", "occurrence": 0}])
    (span,) = told["decision"]["spans"]
    assert render()[span[0] : span[1]] == command


def test_what_cannot_be_bound_exactly_says_why(played):
    root, laid, summaries = played
    ask = ("a", "ask", "prompt")
    refused = {
        "no_such_session": ("z", "ask", "prompt", 0, 3),
        "unsupported_field": ("a", "plant", "content", 0, 3),
        "no_such_field": ("a", "ask", "run", 0, 3),
        "no_such_interval": (*ask, 3, 3),
    }
    for why, where in refused.items():
        assert root_spans.annotation(laid, summaries, *where) == (None, why)
    # A summary that holds other characters than the root wrote (a redaction, a cut).
    other = copy.deepcopy(summaries)
    next(e for e in other["a"]["entries"] if e["type"] == "prompt")["text"] = "[REDACTED]"
    assert root_spans.annotation(laid, other, *ask, 0, 3) == (None, "source_rewritten")
    # A step that was never replayed: it has no event.
    unplayed = copy.deepcopy(laid)
    next(p for p in unplayed["parts"] if p["id"] == "a")["laid"]["where"].pop("ask")
    assert root_spans.annotation(unplayed, summaries, *ask, 0, 3) == (None, "not_replayed")
