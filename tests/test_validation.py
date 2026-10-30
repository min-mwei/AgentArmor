import copy
import json
import math

import pytest

from armor.validation import compare, digest, read, timing_summary

CUTOFFS = {"standard": "v2.2", "bipia": {"cutoff": 0.0}, "toolcall": {"cutoff": 0.5}}


def case_and_response(probability=0.5):
    case = {
        "id": "unicode",
        "request": {
            "state": "A 🦊 note.",
            "questions": {"is_xpia_attempt": {"type": "noul"}},
        },
        "probabilities": {"is_xpia_attempt": 0.5},
        "decision": "clean",
        "latency_probe": True,
    }
    response = {
        "answers": {
            "is_xpia_attempt": {
                "type": "noul",
                "noul": probability,
                "probabilities": {"false": 1 - probability, "true": probability},
                "evidence_spans": [[2, 3]],
            }
        },
        "evidence_available": True,
        "evidence_status": "diagnostic",
        "usage": {"output_tokens": 0},
    }
    return case, response


def test_a_small_probability_gap_does_not_excuse_a_decision_flip():
    case, response = case_and_response(0.5004)
    result = compare(case, response, CUTOFFS)
    assert result["max_abs_dp"] < 0.001
    assert not result["decision_agreed"] and not result["ok"]


def test_unicode_codepoint_offsets():
    case, response = case_and_response()
    result = compare(case, response, CUTOFFS)
    assert result["ok"] and result["spans"] == 1


@pytest.mark.parametrize("span", [[-1, 2], [2, 100], [2, 2], [True, 2], [1, 2]])
def test_invalid_or_whitespace_spans_refuse(span):
    case, response = case_and_response()
    response["answers"]["is_xpia_attempt"]["evidence_spans"] = [span]
    with pytest.raises(ValueError, match="offset"):
        compare(case, response, CUTOFFS)


@pytest.mark.parametrize("value", [math.nan, math.inf, -0.1, 1.1, True])
def test_bad_probabilities_refuse(value):
    case, response = case_and_response(value)
    with pytest.raises(ValueError, match="probability"):
        compare(case, response, CUTOFFS)


def test_missing_questions_and_unavailable_evidence_refuse():
    case, response = case_and_response()
    extra = copy.deepcopy(response)
    extra["answers"]["wrong-question"] = extra["answers"].pop("is_xpia_attempt")
    with pytest.raises(ValueError, match="IDs"):
        compare(case, extra, CUTOFFS)
    response["evidence_status"] = "unavailable"
    with pytest.raises(ValueError, match="diagnostic"):
        compare(case, response, CUTOFFS)


def test_nearest_rank_p95():
    assert timing_summary(list(range(1, 21))) == {
        "requests": 20,
        "median_s": 10.5,
        "p95_s": 19,
        "max_s": 20,
    }


@pytest.fixture
def validation_read(tmp_path, monkeypatch):
    from conftest import make_kai_package

    import armor.kai

    case, response = case_and_response()
    package = make_kai_package(tmp_path)
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(
        json.dumps(
            {
                "schema": "agentarmor-kai-parity-v1",
                "package_manifest_sha256": digest(package / "manifest.json"),
                "reference_identity": {"fixture": True},
                "cases": [case],
            }
        )
    )
    responses = [response]

    def decide(self, request):
        return responses.pop(0) if len(responses) > 1 else responses[0]

    monkeypatch.setattr(
        armor.kai,
        "KaiBackend",
        lambda settings: type("FixtureBackend", (), {"decide": decide})(),
    )
    return tmp_path, fixtures, responses


def test_read_uses_the_api_and_records_failures_without_a_model(validation_read):
    tmp_path, fixtures, responses = validation_read
    # The real ASGI request/schema/serialization path runs; no GPU library is imported.
    report = read(tmp_path, fixtures, tmp_path / "report.json", repeats=2)
    assert report["ok"] and report["records"] == report["questions"] == 1
    assert report["latency"]["unicode"]["requests"] == 2
    with pytest.raises(FileExistsError):
        read(tmp_path, fixtures, tmp_path / "report.json", repeats=2)
    responses[0]["answers"]["is_xpia_attempt"]["evidence_spans"] = [[0, 1000]]
    with pytest.raises(ValueError, match="offset"):
        read(tmp_path, fixtures, tmp_path / "failed.json", repeats=2)
    failed = json.loads((tmp_path / "failed.json").read_text())
    assert failed["ok"] is False and "offset" in failed["error"]


@pytest.mark.parametrize("repeated_probability", [0.4, 0.45])
def test_reference_mismatch_is_distinct_from_repeat_drift(validation_read, repeated_probability):
    tmp_path, fixtures, responses = validation_read
    responses[:] = [case_and_response(0.4)[1], case_and_response(repeated_probability)[1]]
    out = tmp_path / "mismatch.json"
    report = read(tmp_path, fixtures, out, repeats=2)
    assert not report["ok"] and report["decision_disagreements"] == 0
    assert report["records"] == report["questions"] == 1
    assert report["max_abs_dp"] == pytest.approx(0.1)
    assert len(report["repeats"]) == 2 and report["latency"]["unicode"]["requests"] == 2
    for row in report["repeats"]:
        assert not row["ok"] and row["decision_agreed_with_first"]
        assert row["probabilities"]["is_xpia_attempt"]["actual"] == repeated_probability
        assert row["max_abs_dp_from_first"] == pytest.approx(repeated_probability - 0.4)
        assert row["stable_against_first"] == (repeated_probability == 0.4)
    assert json.loads(out.read_text()) == report


def test_late_exception_preserves_first_pass_summary_and_completed_repeats(validation_read):
    tmp_path, fixtures, responses = validation_read
    invalid = case_and_response()[1]
    invalid["answers"]["is_xpia_attempt"]["evidence_spans"] = [[0, 1000]]
    responses[:] = [case_and_response()[1], case_and_response()[1], invalid]
    out = tmp_path / "late-error.json"
    with pytest.raises(ValueError, match="offset"):
        read(tmp_path, fixtures, out, repeats=2)
    report = json.loads(out.read_text())
    assert not report["ok"] and "offset" in report["error"]
    assert report["records"] == report["questions"] == 1
    assert report["max_abs_dp"] == 0 and report["decision_disagreements"] == 0
    assert len(report["repeats"]) == 1
    assert report["repeats"][0]["stable_against_first"]
    assert report["latency"]["unicode"]["requests"] == 1
