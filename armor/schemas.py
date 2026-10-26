"""Public decision and hook contracts, independent of a model runtime."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChoiceQuestion(StrictModel):
    type: Literal["choice"]
    instructions: str = ""
    criteria: dict[str, str | None] = Field(min_length=1, max_length=255)


class NoulQuestion(StrictModel):
    type: Literal["noul"]
    instructions: str = ""
    criteria: dict[Literal["false", "true"], str | None] | None = None


class ScoreQuestion(StrictModel):
    type: Literal["score"]
    instructions: str = ""
    criteria: list[str] = Field(min_length=1, max_length=255)


Question = Annotated[ChoiceQuestion | NoulQuestion | ScoreQuestion, Field(discriminator="type")]


class DecisionRequest(StrictModel):
    model: str = "kai"
    state: str = Field(max_length=1_000_000)
    questions: dict[str, Question] = Field(min_length=1, max_length=64)


class GuardRequest(StrictModel):
    agent: Literal["codex_code", "claude_code"]
    hook_event_name: Literal["PreToolUse"] = "PreToolUse"
    policy: str = Field(min_length=1, max_length=100_000)
    task: str = Field(min_length=1, max_length=100_000)
    tool_name: str = Field(min_length=1, max_length=1000)
    tool_input: Any


class GuardResponse(StrictModel):
    model: str
    backend: str
    decision: Literal["allow", "deny"]
    risk_score: float = Field(ge=0, le=1)
    cutoff: float
    experimental: bool = True
    reason: str
    evidence_available: bool
    evidence_status: Literal["diagnostic", "unavailable"] = "unavailable"
    # Half-open Unicode character offsets into the returned canonical state.
    evidence_spans: list[tuple[int, int]]
    state: str


class ScanRequest(StrictModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    source: Literal["text_file", "chat_log", "tool_output"]
    source_name: str = Field(default="", max_length=2000)
    policy: str = Field(min_length=1, max_length=100_000)
    task: str = Field(min_length=1, max_length=100_000)
    history: str = Field(default="", max_length=100_000)


class EvidenceLocation(StrictModel):
    field: Literal["policy", "task", "history", "text"]
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class ScanResult(StrictModel):
    profile: Literal["xpia-document-v0", "xpia-context-v1"] = "xpia-document-v0"
    policy_task_applied: bool = False
    suspicious: bool
    suspicious_probability: float = Field(ge=0, le=1)
    chunks_scanned: int
    evidence_available: bool
    evidence_status: Literal["diagnostic", "unavailable"] = "unavailable"
    evidence_locations: list[EvidenceLocation] = Field(default_factory=list)
    unmapped_evidence_spans: int = 0
    # Offsets index the original submitted text, including for overlapping chunks.
    evidence_spans: list[tuple[int, int]]
    model: str
    backend: str
    experimental: bool = False


class ScanJob(StrictModel):
    id: str
    status: Literal["queued", "running", "completed", "failed"]
    source: str
    source_name: str
    created: float
    result: ScanResult | None = None
    error: str | None = None
