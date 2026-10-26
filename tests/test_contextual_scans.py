import pytest

from armor.config import Settings
from armor.scans import render_context, scan_text
from armor.schemas import ScanRequest


def test_context_is_rendered_and_unicode_spans_map_to_each_original_field(scan_body):
    scan_body.update(text="🙂 attack", policy="protect é", task="summarize", history="earlier turn")
    request = ScanRequest(**scan_body)
    seen = []

    class EvidenceBackend:
        def decide(self, req):
            seen.append(req)
            spans = [
                (req.state.index(word), req.state.index(word) + len(word))
                for word in ("protect é", "attack", "earlier turn", "Operator policy")
            ]
            return {
                "answers": {"is_xpia_attempt": {"noul": 0.9, "evidence_spans": spans}},
                "evidence_available": True,
            }

    settings = Settings(_env_file=None, backend="stub", scan_profile="xpia-context-v1")
    cutoffs = {"xpia_context": {"profile": "xpia-context-v1", "cutoff": 0.0}}
    result = scan_text(EvidenceBackend(), request, settings, cutoffs)
    assert result.policy_task_applied and result.experimental
    assert result.evidence_status == "diagnostic"
    assert result.evidence_spans == [(2, 8)]
    assert result.unmapped_evidence_spans == 1
    assert {(x.field, x.start, x.end) for x in result.evidence_locations} == {
        ("text", 2, 8),
        ("policy", 0, 9),
        ("history", 0, 12),
    }
    assert "Agent task (trusted):\nsummarize" in seen[0].state
    changed, _ = render_context(
        request.model_copy(update={"policy": "another rule"}), request.text, 0
    )
    assert changed != seen[0].state


def test_context_span_projection_keeps_full_chunk_coverage(scan_body):
    scan_body["text"] = "a" * 120 + "attack" + "b" * 170
    request = ScanRequest(**scan_body)

    class Backend:
        def decide(self, req):
            start = req.state.find("attack", req.state.index("Content:\n"))
            return {
                "answers": {
                    "is_xpia_attempt": {
                        "noul": 0.9,
                        "evidence_spans": [(start, start + 6)] if start >= 0 else [],
                    }
                },
                "evidence_available": True,
            }

    settings = Settings(
        _env_file=None,
        backend="stub",
        scan_profile="xpia-context-v1",
        scan_chunk_chars=128,
        scan_overlap_chars=32,
    )
    cuts = {"xpia_context": {"profile": "xpia-context-v1", "cutoff": 0.0}}
    result = scan_text(Backend(), request, settings, cuts)
    assert result.evidence_spans == [(120, 126)]
    assert result.chunks_scanned == 3


def test_no_contextual_verdict_or_model_call_without_profile_cutoff(scan_body):
    class Backend:
        def decide(self, request):
            raise AssertionError("must refuse before model invocation")

    settings = Settings(_env_file=None, backend="stub", scan_profile="xpia-context-v1")
    with pytest.raises(ValueError, match="registered"):
        scan_text(Backend(), ScanRequest(**scan_body), settings, {"bipia": {"cutoff": -7.0}})
