"""Bounded, in-process asynchronous scans of text files, tool output, and chat logs."""

import asyncio
import json
import logging
import time
import uuid

from armor.backend import Backend
from armor.candidate import load_contract, scan_cutoffs
from armor.config import Settings
from armor.schemas import ScanJob, ScanRequest, ScanResult
from armor.v0 import xpia_flagged, xpia_request

logger = logging.getLogger(__name__)


def text_chunks(text: str, size: int, overlap: int):
    if not 0 <= overlap < size:
        raise ValueError("scan_overlap_chars must be smaller than scan_chunk_chars")
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        yield start, text[start:end]
        if end == len(text):
            break
        start = end - overlap


def render_context(request: ScanRequest, text: str, offset: int):
    """Versioned contextual state and exact mappings to the four caller-owned strings."""
    pieces, locations, position = [], [], 0

    def append(value):
        nonlocal position
        pieces.append(value)
        position += len(value)

    for field, header in (
        ("policy", "Operator policy (trusted)"),
        ("task", "Agent task (trusted)"),
        ("history", "Conversation history (context)"),
    ):
        append(header + ":\n")
        value = getattr(request, field)
        locations.append((field, position, position + len(value), 0))
        append(value)
        append("\n\n")
    append(f"Source (untrusted): {request.source} {json.dumps(request.source_name)}\nContent:\n")
    locations.append(("text", position, position + len(text), offset))
    append(text)
    return "".join(pieces), locations


def scan_text(
    backend: Backend, request: ScanRequest, settings: Settings, cutoffs=None
) -> ScanResult:
    cutoffs = scan_cutoffs(
        cutoffs if cutoffs is not None else load_contract(settings.candidate_package),
        settings.scan_profile,
    )
    contextual = settings.scan_profile == "xpia-context-v1"
    maximum, spans, count, evidence, unmapped = 0.0, [], 0, True, 0
    locations = set()
    for offset, text in text_chunks(
        request.text, settings.scan_chunk_chars, settings.scan_overlap_chars
    ):
        state, mapping = (
            render_context(request, text, offset)
            if contextual
            else (text, [("text", 0, len(text), offset)])
        )
        response = backend.decide(xpia_request(state, settings.served_model_name))
        result = response["answers"]["is_xpia_attempt"]
        probability = float(result["noul"])
        if not 0 <= probability <= 1:
            raise ValueError("Invalid scan probability")
        maximum = max(maximum, probability)
        evidence = evidence and response["evidence_available"]
        count += 1
        if xpia_flagged(probability, cutoffs):
            for start, end in result["evidence_spans"]:
                if not 0 <= start < end <= len(state):
                    raise ValueError("Evidence offsets are outside the scanned state")
                covered = 0
                for field, a, b, source_start in mapping:
                    first, last = max(start, a), min(end, b)
                    if first < last:
                        mapped = (source_start + first - a, source_start + last - a)
                        locations.add((field, *mapped))
                        covered += last - first
                        if field == "text":
                            spans.append(mapped)
                # Renderer headings/separators are not characters in a caller's document.
                unmapped += int(covered < end - start)
    merged = []
    for start, end in sorted(set(spans)):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return ScanResult(
        profile=settings.scan_profile,
        policy_task_applied=contextual,
        suspicious=xpia_flagged(maximum, cutoffs),
        suspicious_probability=maximum,
        chunks_scanned=count,
        evidence_available=evidence,
        evidence_status="diagnostic" if evidence else "unavailable",
        evidence_locations=[{"field": f, "start": a, "end": b} for f, a, b in sorted(locations)],
        unmapped_evidence_spans=unmapped,
        evidence_spans=merged,
        model=settings.served_model_name,
        backend=settings.backend,
        experimental=contextual
        or bool(settings.candidate_package)
        or request.source != "text_file"
        or count > 1,
    )


class ScanQueue:
    def __init__(self, backend: Backend, settings: Settings):
        if settings.scan_overlap_chars >= settings.scan_chunk_chars:
            raise ValueError("scan_overlap_chars must be smaller than scan_chunk_chars")
        self.backend, self.settings = backend, settings
        self.cutoffs = load_contract(settings.candidate_package)
        scan_cutoffs(self.cutoffs, settings.scan_profile)
        self.queue: asyncio.Queue[tuple[str, ScanRequest]] = asyncio.Queue(settings.scan_queue_size)
        self.jobs: dict[str, ScanJob] = {}
        self.worker: asyncio.Task | None = None

    def start(self) -> None:
        self.worker = asyncio.create_task(self._work(), name="agentarmor-scans")

    async def close(self) -> None:
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass

    def _prune(self) -> None:
        cutoff = time.time() - self.settings.scan_job_ttl_seconds
        expired = [
            job_id
            for job_id, job in self.jobs.items()
            if job.status in ("completed", "failed") and job.created < cutoff
        ]
        for job_id in expired:
            del self.jobs[job_id]

    def submit(self, request: ScanRequest) -> ScanJob:
        self._prune()
        if self.queue.full() or len(self.jobs) >= self.settings.scan_max_jobs:
            raise asyncio.QueueFull
        job = ScanJob(
            id=f"scan-{uuid.uuid4().hex}",
            status="queued",
            source=request.source,
            source_name=request.source_name,
            created=time.time(),
        )
        self.jobs[job.id] = job
        self.queue.put_nowait((job.id, request))
        return job

    def get(self, job_id: str) -> ScanJob:
        self._prune()
        return self.jobs[job_id]

    async def _work(self) -> None:
        while True:
            job_id, request = await self.queue.get()
            job = self.jobs[job_id]
            job.status = "running"
            try:
                job.result = await asyncio.to_thread(
                    scan_text, self.backend, request, self.settings, self.cutoffs
                )
                job.status = "completed"
            except Exception:
                logger.exception("Background Kai scan failed (job %s)", job_id)
                job.status = "failed"
                job.error = "Kai could not scan the complete input; no clear verdict is available."
            finally:
                self.queue.task_done()
