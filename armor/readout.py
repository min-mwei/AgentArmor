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
    # These values feed calibrated guard thresholds, so retain model precision.
    dist = dict(zip(row.keys, (float(value) for value in probabilities), strict=True))
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

    def __init__(self, weights: dict[str, np.ndarray], hidden_size: int, objective: str = "tags"):
        if objective not in ("tags", "crf"):
            raise ValueError(f"Unknown evidence objective {objective!r}")
        emissions = dict(weights)
        self.transitions = emissions.pop("transitions", None) if objective == "crf" else None
        if objective == "crf" and (
            self.transitions is None
            or self.transitions.shape != (3, 3)
            or not np.isfinite(self.transitions).all()
        ):
            raise ValueError("CRF transitions must be finite with shape (3, 3)")
        self.dp, hidden = validate_weights(emissions, ("q_b", "q_i", "q_o", "k"))
        if hidden != hidden_size:
            raise ValueError("Evidence and pointer hidden sizes differ")
        self.weights = weights

    def spans(self, hidden: np.ndarray, row: Row, state: str) -> list[tuple[int, int]]:
        keys = linear(hidden[: len(row.offsets)], self.weights, "k")
        queries = np.stack(
            [linear(hidden[-1], self.weights, name) for name in ("q_b", "q_i", "q_o")]
        )
        logits = keys @ queries.T
        tags = (
            logits.argmax(axis=-1)
            if self.transitions is None
            else bio_viterbi(logits * (1 / math.sqrt(self.dp)), self.transitions)
        )
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


def load_evidence(snapshot, meta: dict, hidden_size: int):
    """Missing legacy metadata means tags; an explicit CRF needs its exact sidecar."""
    args = meta.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("Evidence objective metadata args must be an object")
    objective = args.get("evidence_objective", "tags")
    readout = args.get("evidence_readout", "linear")
    pairs = readout in ("joint_pairs", "joint_pairs_memory")
    if pairs != (objective == "pairs"):
        raise ValueError("Pair evidence requires matching readout and objective metadata")
    if not pairs and (readout != "linear" or objective not in ("tags", "crf")):
        raise ValueError(f"Unsupported evidence readout/objective {readout!r}/{objective!r}")
    path = snapshot / "evidence_head.safetensors"
    if not path.exists():
        if objective in ("crf", "pairs"):
            raise ValueError(f"{objective.upper()} metadata requires an evidence sidecar")
        return None
    from safetensors.numpy import load_file

    weights = load_file(path)
    if pairs:
        from armor.readout_pairs import PairEvidenceReadout

        return PairEvidenceReadout(weights, hidden_size, memory=readout == "joint_pairs_memory")
    return EvidenceReadout(weights, hidden_size, objective)


def bio_viterbi(logits, transitions):
    """Kai's complete legal BIO path; first-index argmax breaks ties, marker fixed O."""
    z = np.asarray(logits, dtype=np.float64)
    a = np.array(transitions, dtype=np.float64, copy=True)
    if z.ndim != 2 or z.shape[1] != 3 or a.shape != (3, 3) or len(z) < 1:
        raise ValueError("BIO shape mismatch")
    if not np.isfinite(z).all() or not np.isfinite(a).all():
        raise ValueError("Nonfinite CRF parameter or emission")
    a[2, 1] = -np.inf
    score, parents = np.array([-np.inf, -np.inf, 0.0]), []
    for row in z[1:]:
        edge = score[:, None] + a
        parent = edge.argmax(axis=0)
        score = edge[parent, np.arange(3)] + row
        parents.append(parent)
    tags = [2] * len(z)
    if parents:
        tag = int(score.argmax())
        for pos in range(len(z) - 1, 0, -1):
            tags[pos] = tag
            tag = int(parents[pos - 1][tag])
        assert tag == 2
    return tags
