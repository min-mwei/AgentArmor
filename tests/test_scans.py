import asyncio

import pytest

from armor.config import Settings
from armor.scans import ScanQueue, scan_text, text_chunks
from armor.schemas import ScanRequest


def test_chunks_cover_entire_input_with_overlap():
    text = "0123456789" * 40
    windows = list(text_chunks(text, 128, 16))
    coverage = set()
    for start, chunk in windows:
        assert chunk == text[start : start + len(chunk)]
        coverage.update(range(start, start + len(chunk)))
    assert coverage == set(range(len(text)))
    assert windows[1][0] == 112


def test_offsets_map_to_original_text_and_duplicate_spans_merge(scan_body):
    class EvidenceBackend:
        def decide(self, request):
            start = request.state.find("attack")
            return {
                "answers": {
                    "is_xpia_attempt": {
                        "noul": 0.9 if start >= 0 else 0.0,
                        "evidence_spans": [(start, start + 6)] if start >= 0 else [],
                    }
                },
                "evidence_available": True,
            }

    scan_body["text"] = "a" * 120 + "attack" + "b" * 160
    settings = Settings(_env_file=None, backend="stub", scan_chunk_chars=128, scan_overlap_chars=32)
    result = scan_text(EvidenceBackend(), ScanRequest(**scan_body), settings)
    assert result.suspicious is True
    assert result.evidence_spans == [(120, 126)]
    assert result.chunks_scanned == 3
    assert result.experimental is True


def test_queue_capacity_and_expiry(scan_body, settings):
    from armor.backend import StubBackend

    queue = ScanQueue(StubBackend(), settings.model_copy(update={"scan_queue_size": 1}))
    job = queue.submit(ScanRequest(**scan_body))
    with pytest.raises(asyncio.QueueFull):
        queue.submit(ScanRequest(**scan_body))
    job.status, job.created = "completed", 0
    with pytest.raises(KeyError):
        queue.get(job.id)


def test_invalid_overlap_refused(settings):
    with pytest.raises(ValueError, match="smaller"):
        list(text_chunks("input", 10, 10))
