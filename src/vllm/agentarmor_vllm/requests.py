"""Admit complete requests for the resident Kai model."""

from .api import SystemOneRequest
from .encode import canonical
from .wire import check_json

MAX_TOTAL_TOKENS = 131072
KAI_MODELS = ("kai", "kai-4b", "Kai-4B")


def validate_request(value):
    check_json(value)
    if not isinstance(value, dict) or set(value) - {"state", "questions", "model"}:
        raise ValueError("unsupported request fields")
    if value.get("model", KAI_MODELS[0]) not in KAI_MODELS:
        raise ValueError("request model differs from the resident model")
    if not isinstance(value.get("questions"), dict) or not 1 <= len(value["questions"]) <= 64:
        raise ValueError("request needs 1..64 questions")
    for q in value["questions"].values():
        if not isinstance(q, dict) or set(q) - {"type", "instructions", "criteria"}:
            raise ValueError("unsupported question fields")
        if q.get("type") == "noul" and q.get("criteria") is not None:
            if not isinstance(q["criteria"], dict) or set(q["criteria"]) - {"false", "true"}:
                raise ValueError("noul criteria must use false/true keys")
    # Validate before canonicalization so malformed criteria cannot reach dict.items().
    req = SystemOneRequest.model_validate({**value, "model": value.get("model", KAI_MODELS[0])})
    return SystemOneRequest.model_validate(canonical(req.model_dump()))
