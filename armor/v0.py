"""Frozen Kai v0 questions and decision rules, separate from the GPU runtime."""

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from armor.schemas import DecisionRequest, GuardRequest

CUTOFFS_SHA256 = "b5f584de9af4ca55939f5a82e3281998248811a6b060d79da76e23ad662e4704"
QUESTIONS_SHA256 = "667cfc91dab011764ba11bb797a22c798bdf09376cae062c03ff1a9ed65cca77"
RESOURCE_DIR = Path(__file__).parent / "resources" / "kai_v0"


def verified_json(path: Path, digest: str) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"Kai v0 scoring artifact differs from its pin: {path.name}")
    return json.loads(raw)


def contract(directory: Path = RESOURCE_DIR) -> tuple[dict, dict]:
    return (
        verified_json(directory / "cutoffs.json", CUTOFFS_SHA256),
        verified_json(directory / "v0_questions.json", QUESTIONS_SHA256),
    )


def rule_question_id(rule_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", rule_id.lower()).strip("_")
    return f"rule_{slug}_matches"


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:_-]*$", max_length=100)
    tier: Literal["hard_deny", "soft_deny", "allow"]
    description: str = Field(min_length=1, max_length=10_000)


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: dict[str, str] = Field(default_factory=dict)
    rules: list[Rule] = Field(min_length=1, max_length=62)

    @model_validator(mode="after")
    def distinct_rules(self):
        ids = [rule_question_id(rule.id) for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("Policy rule IDs must have distinct normalized question IDs")
        return self


def guard_request(request: GuardRequest, model: str) -> tuple[DecisionRequest, dict[str, str]]:
    policy = Policy.model_validate_json(request.policy)
    _, questions = contract()
    canonical = questions["toolcall"]
    lines = ["Armor rules (classifier_context v1, auto_mode):", "environment:"]
    lines.extend(f"   - {name}: {value}" for name, value in policy.environment.items())
    asked, tiers = {}, {}
    for tier in ("allow", "hard_deny", "soft_deny"):
        lines.append(tier + ":")
        for rule in policy.rules:
            if rule.tier != tier:
                continue
            lines.append(f"   - {rule.id}: {rule.description}")
            qid = rule_question_id(rule.id)
            asked[qid] = {
                **canonical["rule"],
                "instructions": canonical["rule"]["instructions"].replace("{id}", rule.id),
            }
            tiers[qid] = tier
    lines.extend(
        [
            "",
            "Conversation so far:",
            f"   > user: {request.task}",
            "",
            "Proposed tool call:",
            f"   {request.tool_name} <= {json.dumps(request.tool_input, ensure_ascii=False)}",
        ]
    )
    for name in ("active_user_boundary", "user_requested_exact_operation"):
        asked[name] = canonical[name]
    return DecisionRequest(model=model, state="\n".join(lines), questions=asked), tiers


def toolcall_score(
    answers: dict, tiers: dict[str, str], cutoffs: dict | None = None
) -> tuple[float, float]:
    def probability(question_id: str) -> float:
        value = float(answers[question_id]["noul"])
        if not 0 <= value <= 1:
            raise ValueError("Invalid Kai probability")
        return value

    def maximum(tier: str) -> float:
        return max((probability(qid) for qid in tiers if tiers[qid] == tier), default=0.0)

    boundary = probability("active_user_boundary")
    exact = probability("user_requested_exact_operation")
    cutoffs = cutoffs if cutoffs is not None else contract()[0]
    standard = cutoffs.get("standard", "v2.1")
    if standard not in ("v2.1", "v2.2"):
        raise ValueError("Unknown ToolCall policy standard")
    soft = maximum("soft_deny")
    overridable = soft if standard == "v2.2" else max(soft, boundary)
    score = max(
        maximum("hard_deny"),
        boundary if standard == "v2.2" else 0.0,
        min(overridable, 1 - maximum("allow"), 1 - exact),
    )
    return score, cutoffs["toolcall"]["cutoff"]


def xpia_request(text: str, model: str) -> DecisionRequest:
    _, questions = contract()
    return DecisionRequest(model=model, state=text, questions=questions["xpia"])


def xpia_flagged(probability: float, cutoffs: dict | None = None) -> bool:
    if not 0 <= probability <= 1:
        raise ValueError("Invalid Kai probability")
    probability = min(max(probability, 1e-12), 1 - 1e-12)
    cutoffs = cutoffs if cutoffs is not None else contract()[0]
    return math.log(probability / (1 - probability)) > cutoffs["bipia"]["cutoff"]
