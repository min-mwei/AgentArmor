"""Kai decision API and the shared tool authorization boundary."""

import asyncio
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException

from armor.backend import Backend, ReferenceBackend, StubBackend, VLLMBackend
from armor.candidate import load_contract
from armor.config import Settings
from armor.scans import ScanQueue
from armor.schemas import (
    DecisionRequest,
    GuardRequest,
    GuardResponse,
    ScanJob,
    ScanRequest,
    ScanResult,
)
from armor.v0 import guard_request, toolcall_score

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, backend: Backend | None = None) -> FastAPI:
    settings = settings or Settings()
    cutoffs = load_contract(settings.candidate_package)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        constructors = {"stub": StubBackend, "vllm": VLLMBackend, "reference": ReferenceBackend}
        app.state.backend = backend or (
            StubBackend()
            if settings.backend == "stub"
            else constructors[settings.backend](settings)
        )
        app.state.scans = ScanQueue(app.state.backend, settings)
        app.state.scans.start()
        try:
            yield
        finally:
            await app.state.scans.close()
            app.state.backend = None

    app = FastAPI(title="AgentArmor", version="0.1.0.dev0", lifespan=lifespan)

    def authorize(authorization: str | None = Header(default=None)) -> None:
        if settings.api_key:
            expected = f"Bearer {settings.api_key.get_secret_value()}"
            if not hmac.compare_digest(authorization or "", expected):
                raise HTTPException(status_code=401, detail="Invalid API key")

    @app.get("/healthz")
    def health() -> dict:
        return {"status": "ok", "backend": settings.backend}

    @app.get("/readyz", dependencies=[Depends(authorize)])
    def ready() -> dict:
        if not getattr(app.state, "backend", None):
            raise HTTPException(status_code=503, detail="Model is not ready")
        return {"status": "ready", "model": settings.served_model_name, "backend": settings.backend}

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    def models() -> dict:
        return {
            "object": "list",
            "data": [
                {
                    "id": settings.served_model_name,
                    "object": "model",
                    "owned_by": "agentarmor",
                }
            ],
        }

    async def run_decision(request: DecisionRequest) -> dict:
        if request.model not in (settings.served_model_name, settings.model):
            raise HTTPException(status_code=404, detail="Unknown model")
        try:
            result = await asyncio.to_thread(app.state.backend.decide, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Kai inference failed")
            raise HTTPException(status_code=503, detail="Kai inference unavailable") from exc
        return {
            "id": f"decision-{uuid.uuid4().hex}",
            "object": "structured_decision",
            "created": int(time.time()),
            "model": settings.served_model_name,
            "backend": settings.backend,
            "experimental": bool(settings.candidate_package) or settings.backend == "reference",
            **result,
            "evidence_status": "diagnostic" if result["evidence_available"] else "unavailable",
        }

    @app.post("/v1/systemone", dependencies=[Depends(authorize)])
    async def decide(request: DecisionRequest) -> dict:
        return await run_decision(request)

    @app.post(
        "/v1/scans", response_model=ScanJob, status_code=202, dependencies=[Depends(authorize)]
    )
    async def submit_scan(request: ScanRequest) -> ScanJob:
        try:
            return app.state.scans.submit(request)
        except asyncio.QueueFull as exc:
            raise HTTPException(status_code=429, detail="Scan queue is full; retry later") from exc

    @app.get("/v1/scans/{job_id}", response_model=ScanJob, dependencies=[Depends(authorize)])
    async def get_scan(job_id: str) -> ScanJob:
        try:
            return app.state.scans.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown or expired scan") from exc

    @app.get(
        "/v1/scans/{job_id}/result", response_model=ScanResult, dependencies=[Depends(authorize)]
    )
    async def get_scan_result(job_id: str) -> ScanResult:
        job = await get_scan(job_id)
        if job.status == "failed":
            raise HTTPException(status_code=503, detail=job.error)
        if job.result is None:
            raise HTTPException(status_code=409, detail=f"Scan is {job.status}")
        return job.result

    @app.post("/v1/guard", response_model=GuardResponse, dependencies=[Depends(authorize)])
    async def guard(request: GuardRequest) -> GuardResponse:
        try:
            decision_request, tiers = guard_request(request, settings.served_model_name)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Kai v0 requires a JSON policy with explicit rule IDs and tiers",
            ) from exc
        result = await run_decision(decision_request)
        score, cutoff = toolcall_score(result["answers"], tiers, cutoffs)
        denied = score > cutoff
        reason = (
            f"AgentArmor denied this tool call ({cutoffs.get('standard', 'v2.1')} "
            f"composite {score:.4f} > {cutoff:.4f})."
            if denied
            else "AgentArmor found no policy violation; normal agent permissions still apply."
        )
        if settings.backend == "stub":
            reason = "AgentArmor smoke backend is active; all tool calls are denied."
        return GuardResponse(
            model=settings.served_model_name,
            backend=settings.backend,
            decision="deny" if denied else "allow",
            risk_score=score,
            cutoff=cutoff,
            reason=reason,
            evidence_available=result["evidence_available"],
            evidence_status=result["evidence_status"],
            evidence_spans=sorted(
                {
                    tuple(span)
                    for value in result["answers"].values()
                    for span in value.get("evidence_spans", [])
                }
            ),
            state=decision_request.state,
        )

    return app
