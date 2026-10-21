"""CPU readouts over unnormalized final hidden states; no token generation."""

import math

import numpy as np

from armor.encoding import Row


def validate_weights(weights: dict, projections: tuple[str, ...]) -> tuple[int, int]:
    expected = {f"{projection}.{part}" for projection in projections for part in ("weight", "bias")}
    if set(weights) != expected:
        raise ValueError(f"Invalid head tensors: expected {sorted(expected)}")
    dp, hidden = weights[f"{projections[0]}.weight"].shape
    for projection in projections:
        if weights[f"{projection}.weight"].shape != (dp, hidden) or weights[
            f"{projection}.bias"
        ].shape != (dp,):
            raise ValueError(f"Invalid head shape for {projection}")
    if not all(np.isfinite(value).all() for value in weights.values()):
        raise ValueError("Head tensors contain non-finite values")
    return dp, hidden


def linear(value: np.ndarray, weights: dict, name: str) -> np.ndarray:
    return value @ weights[f"{name}.weight"].T + weights[f"{name}.bias"]


class PointerReadout:
    def __init__(self, weights: dict[str, np.ndarray], temperature: float = 1.0):
        self.dp, self.hidden_size = validate_weights(weights, ("q", "k"))
        if not math.isfinite(temperature) or temperature <= 0:
            raise ValueError("Checkpoint temperature must be finite and positive")
        self.weights, self.temperature = weights, temperature

    def probabilities(self, hidden: np.ndarray, row: Row) -> np.ndarray:
        if hidden.shape != (len(row.token_ids), self.hidden_size):
            raise ValueError("vLLM did not return one raw hidden state per input token")
        query = linear(hidden[-1], self.weights, "q")
        keys = linear(hidden[row.option_indices], self.weights, "k")
        logits = (keys @ query) / (math.sqrt(self.dp) * self.temperature)
        if not np.isfinite(logits).all():
            raise ValueError("Non-finite decision logits")
        probabilities = np.exp(logits - logits.max())
        return probabilities / probabilities.sum()


def answer(row: Row, probabilities: np.ndarray) -> dict:
    dist = dict(zip(row.keys, (round(float(value), 4) for value in probabilities), strict=True))
    result = {"type": row.question_type, "probabilities": dist, "evidence_spans": []}
    if row.question_type == "choice":
        count = len(probabilities)
        result["choice"] = row.keys[int(probabilities.argmax())]
        result["confidence"] = (
            1.0 if count == 1 else float((probabilities.max() - 1 / count) / (1 - 1 / count))
        )
    elif row.question_type == "noul":
        result["noul"] = dist["true"]
    else:
        result["score"] = float(np.dot(np.arange(len(probabilities)), probabilities))
        result["legend"] = row.legend
    return result


class EvidenceReadout:
    """Kai's B/I/O evidence head, with offsets into the exact submitted state."""

    def __init__(self, weights: dict[str, np.ndarray], hidden_size: int):
        self.dp, hidden = validate_weights(weights, ("q_b", "q_i", "q_o", "k"))
        if hidden != hidden_size:
            raise ValueError("Evidence and pointer hidden sizes differ")
        self.weights = weights

    def spans(self, hidden: np.ndarray, row: Row, state: str) -> list[tuple[int, int]]:
        keys = linear(hidden[: len(row.offsets)], self.weights, "k")
        queries = np.stack(
            [linear(hidden[-1], self.weights, name) for name in ("q_b", "q_i", "q_o")]
        )
        tags = (keys @ queries.T).argmax(axis=-1)
        spans, start, end = [], None, None

        def finish() -> None:
            if start is not None and end is not None:
                first, last = row.offsets[start][0], row.offsets[end][1]
                text = state[first:last]
                first += len(text) - len(text.lstrip())
                last -= len(text) - len(text.rstrip())
                if first < last:
                    spans.append((first, last))

        for index, tag in enumerate(tags[1:], start=1):
            if tag == 0 or (tag == 1 and start is None):
                finish()
                start = end = index
            elif tag == 1:
                end = index
            else:
                finish()
                start = end = None
        finish()
        return spans
