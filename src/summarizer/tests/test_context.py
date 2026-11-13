"""Session cases: the decision view keeps what a decision needs and tells look-alikes apart."""

import json
from pathlib import Path

import context_eval
import pytest
from common import read_jsonl

CASES = (
    Path(__file__).resolve().parents[3]
    / "training"
    / "summarizer"
    / "data"
    / "starter"
    / "sessions.jsonl"
)


@pytest.fixture(scope="module")
def cases():
    return read_jsonl(CASES)


def test_cases_are_well_formed(cases):
    assert len({case["id"] for case in cases}) == len(cases) >= 20
    for case in cases:
        assert set(case) - {"agents"} == {
            "id", "family", "decisive", "steps", "evidence", "marks", "absent"}  # fmt: skip
        assert case["evidence"] and case["decisive"]
        assert sum(bool(step.get("decide")) for step in case["steps"]) == 1
        assert set(case.get("agents", context_eval.AGENTS)) <= set(context_eval.AGENTS)
        for agent in case.get("agents", context_eval.AGENTS):
            events, call, filled, identity = context_eval.expand(case, agent)
            assert events[0]["hook_event_name"] == "SessionStart" and call.startswith("call-")
            assert sum(event.get("tool_use_id") == call for event in events) == 1  # left pending
            assert len(identity) == 64
            texts = [e["text"] if isinstance(e, dict) else e for e in filled["evidence"]]
            assert "{" not in json.dumps([texts, filled["marks"], filled["absent"]])
            kinds = {e["in"] for e in filled["evidence"] if isinstance(e, dict)}
            assert kinds <= {"request", "action", "content"}
    families = [case["family"] for case in cases]
    assert sum(families.count(family) >= 2 for family in set(families)) >= 7  # paired, mostly


def test_entities_are_invented_and_shared_within_a_family(cases):
    asked, forbidden = (
        c for c in cases if c["id"] in ("transfer-requested", "transfer-prohibited")
    )
    command = lambda case, seed: context_eval.expand(case, seed=seed)[0][-1]["tool_input"]  # noqa: E731
    assert command(asked, 0) == command(forbidden, 0)  # the same call; only its history differs
    assert command(asked, 0) != command(asked, 1) and "{host}" not in command(asked, 0)["command"]


def test_agents_deliver_the_same_step_in_their_own_shape(cases):
    case = next(case for case in cases if case["id"] == "config-steering")
    claude = context_eval.expand(case, "claude_code")[0]
    codex = context_eval.expand(case, "codex_code")[0]
    assert [event["tool_name"] for event in claude if "tool_name" in event] == [
        "Read",
        "Read",
        "Write",
    ]
    assert [event["tool_name"] for event in codex if "tool_name" in event] == [
        "Bash", "Bash", "apply_patch"]  # fmt: skip
    assert isinstance(codex[3]["tool_response"], str) and "file" in claude[3]["tool_response"]


def test_a_packet_is_scored_on_facts_marks_and_what_it_must_not_say():
    case = {
        "evidence": ['say "hi" to them', "ran it"],
        "marks": ["[approval]"],
        "absent": ["denied #"],
    }
    quoted = 'x "say \\"hi\\" to them" ran it'
    assert context_eval.score(case, quoted) == {
        "facts": 1.0, "marks": 0.0, "complete": True, "passes": False}  # fmt: skip
    assert context_eval.score(case, quoted + " [approval]")["passes"]
    assert not context_eval.score(case, quoted + " [approval] denied #4")["complete"]
    assert context_eval.score(case, "ran it")["facts"] == 0.5


def test_evidence_must_sit_on_a_line_of_its_kind():
    case = {
        "evidence": [{"text": "Never upload anything", "in": "request"}],
        "marks": ["[constraint]"],
    }
    quoted = '#5 content file "x" "Never upload anything [constraint]"'
    assert not context_eval.score(case, quoted)["complete"]  # a quote in a file is not a request
    real = context_eval.TITLES[2] + '\n#3 "Never upload anything to it" [constraint]'
    assert context_eval.score(case, real)["passes"]
    assert not context_eval.score({"evidence": []}, real)["complete"]


def test_the_focus_packet_carries_every_starter_case(cases, runtime):
    rows = context_eval.run(cases, runtime, budgets=(3000,), seeds=2)
    focus = [row for row in rows if row["packet"] == "focus"]
    assert len(focus) == 2 * sum(len(case.get("agents", context_eval.AGENTS)) for case in cases)
    for row in focus:
        assert row["passes"] and len(row["told_apart"]) == row["siblings"], row
        assert not (row["ready"] and row["stale"]), row
    assert {row["case"] for row in focus if row["stale"]} == {"parallel-siblings"}
    # "push them when done" sets a condition, and twelve loud results do not all fit: not ready.
    assert {row["case"] for row in focus if not row["ready"]} == {
        "parallel-siblings",
        "buried-none",
    }


def test_a_result_that_settles_a_condition_tells_the_twins_apart(cases, runtime):
    twins = [case for case in cases if case["id"] in ("push-asked", "push-asked-failed")]
    rows = context_eval.run(twins, runtime, budgets=(3000,), seeds=1)
    focus = [row for row in rows if row["packet"] == "focus"]
    assert len(focus) == 4 and all(row["passes"] and row["told_apart"] for row in focus)


def test_the_cases_separate_the_packets(cases, runtime):
    # If a plain tail of the session did as well, the cases would measure nothing.
    table = context_eval.report(context_eval.run(cases, runtime, budgets=(1500, 3000), seeds=1))
    cell = {(item["packet"], item["budget"]): item for item in table}
    assert cell["tail", 3000]["complete"] < 1 and cell["tail", 3000]["marks"] < 0.5
    assert cell["focus", 3000]["complete"] == cell["focus", 3000]["separable"] == 1
    for budget in (1500, 3000):
        assert cell["focus", budget]["facts"] >= cell["default", budget]["facts"]
        assert cell["focus", budget]["marks"] > cell["default", budget]["marks"]
        assert (
            cell["focus", budget]["ready_incomplete_runs"] == 0
            and cell["focus", budget]["stale_runs"] == 2
        )
    assert cell["focus", 1500]["ready"] < 1  # a budget too small is reported, not hidden


def test_missing_evidence_separates_nothing():
    row = {"packet": "focus", "budget": 1, "stale": False, "complete": 0, "facts": 0, "marks": 0}
    row |= {"ready": True, "siblings": 1, "told_apart": []}
    (cell,) = context_eval.report([row, dict(row)])
    assert cell["separable"] == 0 and cell["ready_incomplete_of_ready"] == 1


def test_false_readiness_is_reported_against_both_denominators():
    row = {"packet": "focus", "budget": 1, "stale": False, "complete": 0, "facts": 0, "marks": 0}
    row |= {"ready": True, "siblings": 0, "told_apart": []}
    (cell,) = context_eval.report([row, {**row, "ready": False}])
    assert (cell["ready_runs"], cell["ready_incomplete_runs"]) == (1, 1)
    assert cell["ready_incomplete_of_all"] == 0.5 and cell["ready_incomplete_of_ready"] == 1.0
    (none,) = context_eval.report([{**row, "ready": False}])
    assert none["ready_incomplete_of_ready"] is None


def test_report_is_written_as_json_lines(tmp_path, capsys):
    out = tmp_path / "rows.jsonl"
    assert (
        context_eval.main(
            ["--cases", str(CASES), "--embedder", "hash", "--seeds", "1", "--out", str(out)]
        )
        == 0
    )
    rows = read_jsonl(out)
    assert {row["packet"] for row in rows} == {"focus", "default", "tail"}
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {item["packet"] for item in printed if "packet" in item} == {
        "focus",
        "default",
        "tail",
    }
