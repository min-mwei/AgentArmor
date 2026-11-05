"""Kai api contract adapted from the trainer snapshot; see src/ORIGIN.md."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

JSONContent = str | dict | list | int | float | bool | None
MAX_OPTIONS = 255


class Noul(BaseModel):
    type: Literal["noul"]
    instructions: JSONContent = None
    criteria: dict[str, JSONContent] | None = None


class Choice(BaseModel):
    type: Literal["choice"]
    instructions: JSONContent = None
    criteria: dict[str, JSONContent]

    @model_validator(mode="after")
    def _check(self):
        if not 1 <= len(self.criteria) <= MAX_OPTIONS:
            raise ValueError(f"criteria must have 1..{MAX_OPTIONS} options")
        return self


class Score(BaseModel):
    type: Literal["score"]
    instructions: JSONContent = None
    criteria: list[JSONContent] = Field(min_length=1, max_length=MAX_OPTIONS)


Question = Noul | Choice | Score


class SystemOneRequest(BaseModel):
    state: JSONContent
    model: str = "kai"
    questions: dict[str, Question] = Field(min_length=1)


def render(v: JSONContent, indent: int = 0) -> str:
    """Flatten str | object | array into text the model sees. Field names are kept as labels."""
    pad = "  " * indent
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    if isinstance(v, list):
        return "\n".join(f"{pad}- {render(x, indent + 1).lstrip()}" for x in v)
    return "\n".join(
        f"{pad}{k}:\n{render(x, indent + 1)}"
        if isinstance(x, (dict, list))
        else f"{pad}{k}: {render(x)}"
        for k, x in v.items()
    )


def option_text(name: str, desc: JSONContent) -> str:
    return name if desc is None or desc == "" else f"{name}: {render(desc)}"


def question_keys(qtype: str, criteria) -> list[str]:
    """Return answer keys in the exact encoded option order."""
    if qtype == "choice":
        return list(criteria)
    if qtype == "noul":
        return ["false", "true"]
    return [str(i) for i in range(len(criteria))]


def to_record(req: SystemOneRequest):
    """Materialize a request and retain metadata for mapping probabilities back to keys."""
    qs, meta = [], []
    for qid, q in req.questions.items():
        m = {"id": qid, "type": q.type, "keys": question_keys(q.type, q.criteria)}
        if q.type == "noul":
            c = q.criteria or {}
            opts = [option_text("no", c.get("false")), option_text("yes", c.get("true"))]
        elif q.type == "choice":
            opts = [option_text(k, v) for k, v in q.criteria.items()]
        else:
            opts = [render(x) for x in q.criteria]
            m["legend"] = dict(zip(m["keys"], opts, strict=True))
        qs.append({"instr": render(q.instructions), "options": opts, "label": 0})
        meta.append(m)
    return {"state": render(req.state), "questions": qs}, meta


# Both confidence formulas mirror TypeSafe's reference adapter, system-one-adapter 0.2.1
# (src/system_one_adapter/_utils/confidence_metrics.py): p is normalised to sum 1 (all zeros ->
# uniform), one option -> 1.
def _normalize(p: list[float]) -> list[float]:
    t = sum(p)
    return [1 / len(p)] * len(p) if t == 0 else [x / t for x in p]


def choice_confidence(p: list[float]) -> float:
    """(p_max - 1/K) / (1 - 1/K): 0 at uniform, 1 at certainty."""
    K = len(p)
    return 1.0 if K == 1 else (max(_normalize(p)) - 1 / K) / (1 - 1 / K)


def score_confidence(p: list[float]) -> float:
    """max(0, 1 - E|level - mode| / D), D = mean absolute deviation of a uniform distribution."""
    L = len(p)
    if L == 1:
        return 1.0
    p = _normalize(p)
    mode = max(range(L), key=p.__getitem__)
    D = sum(abs(i - (L - 1) / 2) for i in range(L)) / L
    return max(0.0, 1.0 - sum(pi * abs(i - mode) for i, pi in enumerate(p)) / D)


def _display_scalar(x: float) -> float:
    """Retain legacy display precision for derived confidence and score scalars."""
    return round(float(x), 4)


def to_answers(probs: list[list[float]], meta: list[dict]) -> dict[str, Any]:
    out = {}
    for p, m in zip(probs, meta, strict=True):
        if m["type"] == "noul":
            out[m["id"]] = {"type": "noul", "noul": float(p[1])}
        elif m["type"] == "choice":
            dist = {k: float(v) for k, v in zip(m["keys"], p, strict=True)}
            out[m["id"]] = {
                "type": "choice",
                "choice": m["keys"][max(range(len(p)), key=lambda i: p[i])],
                "confidence": _display_scalar(choice_confidence(p)),
                "probabilities": dist,
            }
        else:
            score = sum(i * pi for i, pi in enumerate(p))
            out[m["id"]] = {
                "type": "score",
                "score": _display_scalar(score),
                "legend": m["legend"],
                "probabilities": {str(i): float(v) for i, v in enumerate(p)},
                "confidence": _display_scalar(score_confidence(p)),
            }
    return out
