"""Backend contract and deny-only smoke implementation. Official model execution is in armor.kai."""

from typing import Protocol

from armor.config import Settings
from armor.schemas import DecisionRequest


class Backend(Protocol):
    evidence_available: bool

    def decide(self, request: DecisionRequest) -> dict: ...


def inspect_model(settings: Settings) -> dict:
    from armor.kai import KaiPackage

    package = KaiPackage(settings.candidate_package, settings.package_sha256)
    return {
        "model": "kai",
        "architecture": "embeddinggemma2-text",
        "package_sha256": package.sha256,
        "branches": list(package.branches),
        "files_verified": True,
        "model_loaded": False,
        "note": "Files verified only; model parity and quality require the registered read.",
    }


class StubBackend:
    """Explicit, deny-only smoke backend. Never an approximation of Kai."""

    evidence_available = False

    def decide(self, request: DecisionRequest) -> dict:
        if any(question.type != "noul" for question in request.questions.values()):
            raise ValueError("The stub supports only Kai v0 yes/no questions")
        answers = {}
        for question_id in request.questions:
            value = 0.0 if question_id == "user_requested_exact_operation" else 1.0
            answers[question_id] = {
                "type": "noul",
                "noul": value,
                "probabilities": {"false": 1 - value, "true": value},
                "evidence_spans": [],
            }
        return {
            "answers": answers,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "evidence_available": False,
        }
