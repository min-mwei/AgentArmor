"""The omission check: every fact a label rests on is evidence the admission check can lose and detect."""

import copy
from pathlib import Path

import check_evidence
import pytest
from campaign_contract import layout, reference, replay
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
