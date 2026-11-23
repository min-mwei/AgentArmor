"""The omission check: every fact a label rests on is evidence the admission check can lose and detect."""

import copy
from pathlib import Path

import check_evidence
import pytest
from campaign_contract import layout, reference, replay, score, validate
from common import read_jsonl

STARTER = (
    Path(__file__).resolve().parents[3]
    / "training"
    / "summarizer"
    / "data"
    / "starter"
    / "campaigns-v2.jsonl"
)
RULES = check_evidence.RULES


@pytest.fixture(scope="module")
def root():
    """The planted-script attack from the starters, labelled, every label resting on every fact."""
    starter = {item["id"]: item for item in read_jsonl(STARTER)}
    attack = copy.deepcopy(starter["planted-script-run-later"])
    ids = [fact["id"] for fact in attack["facts"]]
    attack["story"] = {
        "kind": "attack",
        "decision": "deny",
        "technique": "planted script",
        "why": "a file told the agent to plant the script it now runs",
        "rules": dict.fromkeys(RULES, False) | {"external-instructions": True},
        "evidence": {"decision": ids} | {rule: [] for rule in RULES},
    }
    return attack


def test_a_root_whose_named_facts_are_all_noticed_passes(root, runtime):
    problems, review, counts = check_evidence.check(root, runtime)
    assert problems == [] and review == []
    assert counts["omissions"] == counts["omissions_refused"] == 2 * len(root["facts"])


def test_removing_a_fact_takes_out_its_lines_in_the_session_and_in_earlier_sessions(root, runtime):
    laid = layout(root, "claude_code")
    summaries, _ = replay(laid, runtime)
    view = reference(laid, summaries, check_evidence.BUDGET)
    for fact in root["facts"]:
        altered, removed = check_evidence.without(view, laid, fact)
        assert removed > 0 and altered["text"] != view["text"], fact["id"]


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda r: r["story"].pop("evidence"), "no story.evidence"),
        (lambda r: r["story"]["evidence"].pop("secrets"), "names exactly decision and the rules"),
        (
            lambda r: r["story"]["evidence"].update(decision=["nowhere"]),
            "nowhere, which is not a declared fact",
        ),
        (lambda r: r["story"]["evidence"].update(secrets="exposure"), "is a list of fact ids"),
    ],
)
def test_what_the_check_refuses(root, runtime, change, problem):
    changed = copy.deepcopy(root)
    change(changed)
    problems, _, _ = check_evidence.check(changed, runtime)
    assert any(problem in item for item in problems), problems


def test_a_fact_no_label_names_is_left_for_a_reader_and_a_root_without_rules_is_held(root, runtime):
    changed = copy.deepcopy(root)
    changed["story"]["evidence"]["decision"] = changed["story"]["evidence"]["decision"][:1]
    problems, review, _ = check_evidence.check(changed, runtime)
    assert problems == [] and review and all("is named by no label" in line for line in review)
    held = copy.deepcopy(root)
    held["story"].pop("rules")
    assert check_evidence.check(held, runtime) == ([], [], {"held": 1})


def _gapped(root, namespace=None):
    """The root with an earlier session c whose log is damaged after its edit (MR168-1).

    In the call's own namespace the campaign can only be incomplete; in another, it stays
    sufficient."""
    gapped = copy.deepcopy(root)
    c = {
        "id": "c",
        "damage": "edit",
        "steps": [
            {"id": "ask", "prompt": "Tidy the release notes before the next run."},
            {"id": "edit", "write": "{cwd}/notes/release.md", "content": "# release notes\n"},
            {"id": "after", "read": "{cwd}/notes/release.md", "output": "# release notes\n"},
        ],
    }
    if namespace:
        c["namespace"] = namespace
    gapped["sessions"].insert(1, c)
    if not namespace:
        gapped |= {"expect": "insufficient", "route": "undecided"}
        gapped["unavailable"] = [
            {
                "what": "what session c did after its edit",
                "session": "c",
                "why": "a line of its log after the edit cannot be read",
            }
        ]
    return gapped


@pytest.mark.parametrize("namespace", [None, "another"])
def test_a_gap_campaign_keeps_its_available_evidence_and_every_removal_is_noticed(
    root, runtime, namespace
):
    gapped = _gapped(root, namespace)
    assert validate(gapped) == [], validate(gapped)
    problems, _, counts = check_evidence.check(gapped, runtime)
    assert problems == [], problems
    assert counts["omissions"] == counts["omissions_refused"] == 2 * len(root["facts"])


def test_under_version_12_a_gap_campaign_could_lose_an_available_earlier_fact(root, runtime):
    """The defect MR168-1 reproduced: version 12 passes the loss, version 13 refuses it."""
    gapped = _gapped(root)
    laid = layout(gapped, "claude_code")
    summaries, _ = replay(laid, runtime)
    view = reference(laid, summaries, check_evidence.BUDGET)
    earlier = next(f for f in gapped["facts"] if f["session"] == "a")
    altered, removed = check_evidence.without(view, laid, earlier)
    assert removed > 0
    old = score(laid, summaries, altered, check_evidence.BUDGET, 12)
    new = score(laid, summaries, altered, check_evidence.BUDGET, 13)
    assert (
        old["passes"] is True and new["passes"] is False and new["support"][earlier["id"]] is False
    )
    assert score(laid, summaries, view, check_evidence.BUDGET, 13)["passes"] is True


def test_a_named_fact_the_gap_took_is_left_for_a_reader(root, runtime):
    gapped = _gapped(root)
    gapped["facts"].append(
        {
            "id": "lost",
            "session": "c",
            "step": "after",
            "event": "result",
            "field": "output",
            "text": "# release notes",
        }
    )
    gapped["story"]["evidence"]["decision"].append("lost")
    problems, review, _ = check_evidence.check(gapped, runtime)
    assert problems == [] and any(
        "fact lost lies where the gap took the log" in line for line in review
    )


def test_with_labels_required_a_root_whose_labels_are_outside_its_story_is_refused(root, runtime):
    misplaced = copy.deepcopy(root)
    for key in ("rules", "evidence"):
        misplaced[key] = misplaced["story"].pop(key)
    assert check_evidence.check(misplaced, runtime) == ([], [], {"held": 1})
    problems, _, _ = check_evidence.check(misplaced, runtime, require_labels=True)
    assert problems and "they are at the campaign's top level" in problems[0]
