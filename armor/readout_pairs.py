"""Kai joint-pair evidence readouts over the same hidden states as the decision.

NumPy port of Kai's evidence_pairs.py and MemoryPairEvidenceHead, Apache-2.0.
The decoder returns diagnostic spans, not a qualification of their completeness.
"""

import math

import numpy as np

from armor.readout import linear, validate_weights


def normalize(x):
    return (x - x.mean(axis=-1, keepdims=True)) / np.sqrt(
        x.var(axis=-1, keepdims=True) + np.float32(1e-5)
    )


def rope(x):
    length, dim = x.shape
    angle = np.arange(length, dtype=np.float32)[:, None] * (
        10000.0 ** (-2 * np.arange(dim // 2, dtype=np.float32) / dim)
    )
    a, b = x[:, 0::2], x[:, 1::2]
    return np.stack(
        [a * np.cos(angle) - b * np.sin(angle), a * np.sin(angle) + b * np.cos(angle)],
        axis=-1,
    ).reshape(x.shape)


class PairEvidenceReadout:
    def __init__(self, weights, hidden_size, memory):
        projections = {k: v for k, v in weights.items() if k.startswith(("q.", "k."))}
        self.dp, self.hidden_size = validate_weights(projections, ("q", "k"))
        self.prefix = "pair_memory" if memory else "pair"
        self.memory = memory
        self.weights = {k: np.asarray(v, dtype=np.float32) for k, v in weights.items()}
        prefix = self.prefix
        hidden = self.weights.get(f"{prefix}.hidden.weight")
        if hidden is None or hidden.ndim != 2:
            raise ValueError("Pair evidence head has no hidden projection")
        width = hidden.shape[0]
        shapes = {
            "hidden": (width, 3 * self.dp),
            "start": (width, width),
            "end": (width, width),
            "bias": (1, self.dp),
        }
        expected = set(projections)
        for name, shape in shapes.items():
            for part, size in (("weight", shape), ("bias", (shape[0],))):
                key = f"{prefix}.{name}.{part}"
                value = self.weights.get(key)
                if value is None or value.shape != size:
                    raise ValueError(f"Invalid pair evidence tensor {key}")
                expected.add(key)
        if hidden_size != self.hidden_size or width < 2 or width % 2:
            raise ValueError("Invalid pair evidence hidden size or RoPE width")
        if set(weights) != expected or not all(np.isfinite(v).all() for v in weights.values()):
            raise ValueError("Unexpected or nonfinite pair evidence tensors")

    def scores(self, hidden, row):
        hidden = np.asarray(hidden, dtype=np.float32)
        if hidden.shape != (len(row.token_ids), self.hidden_size):
            raise ValueError("Pair evidence needs one hidden state per token")
        n = len(row.offsets)
        if not 1 <= n < len(hidden):
            raise ValueError("Pair evidence needs a state and a nonempty question branch")
        k = normalize(linear(hidden[:n], self.weights, "k"))
        q = normalize(linear(hidden[-1], self.weights, "q"))
        if self.memory:
            memory = normalize(linear(hidden[n:], self.weights, "q"))
            logits = (k @ memory.T) / math.sqrt(self.dp)
            attention = np.exp(logits - logits.max(axis=-1, keepdims=True))
            attention /= attention.sum(axis=-1, keepdims=True)
            context = attention @ memory
        else:
            context = np.broadcast_to(q, k.shape)
        features = np.concatenate([k, context, k * context], axis=-1)
        x = linear(features, self.weights, f"{self.prefix}.hidden")
        u = x * np.exp(-np.logaddexp(0, -x))
        start = rope(linear(u, self.weights, f"{self.prefix}.start"))
        end = rope(linear(u, self.weights, f"{self.prefix}.end"))
        return start @ end.T / math.sqrt(start.shape[-1]) + linear(
            q, self.weights, f"{self.prefix}.bias"
        ).reshape(())

    def spans(self, hidden, row, state):
        return decode_pairs(self.scores(hidden, row), row.offsets, state)


def decode_pairs(scores, offsets, state):
    """Maximum-weight nonoverlapping Unicode intervals; ties retain earlier solutions."""
    z = np.asarray(scores, dtype=np.float64)
    n = len(offsets)
    if z.shape != (n, n) or n < 1:
        raise ValueError("Pair scores and offsets differ")
    valid = (np.arange(n)[:, None] >= 1) & (np.arange(n)[:, None] <= np.arange(n)[None, :])
    if not np.isfinite(z[valid]).all():
        raise ValueError("Nonfinite evidence pair score")
    left, right = np.arange(len(state) + 1), np.arange(len(state) + 1)
    for i in range(len(state) - 1, -1, -1):
        if state[i].isspace():
            left[i] = left[i + 1]
    for i in range(1, len(state) + 1):
        if state[i - 1].isspace():
            right[i] = right[i - 1]
    for a, b in offsets:
        if not 0 <= a <= b <= len(state):
            raise ValueError("Evidence offset outside state")
    starts = left[[a for a, _ in offsets]]
    ends = right[[b for _, b in offsets]]
    boundaries = np.unique(np.concatenate([[0], ends[1:]]))
    previous = np.searchsorted(boundaries, starts, side="right") - 1
    best, back = np.zeros(len(boundaries)), [None] * len(boundaries)
    all_i = np.arange(n)[:, None]
    for step in range(1, len(boundaries)):
        end = boundaries[step]
        js = np.flatnonzero(ends == end)
        usable = (
            (all_i >= 1)
            & (all_i <= js[None, :])
            & (z[:, js] > 0)
            & (starts[:, None] < end)
            & (previous[:, None] < step)
        )
        values = np.where(usable, z[:, js] + best[previous, None], -np.inf)
        if values.size:
            i, j = np.unravel_index(int(np.argmax(values)), values.shape)
            if values[i, j] > best[step - 1]:
                best[step] = values[i, j]
                back[step] = (int(previous[i]), int(starts[i]), int(end))
                continue
        best[step] = best[step - 1]
    spans, step = [], len(boundaries) - 1
    while step:
        if back[step] is None:
            step -= 1
        else:
            step, a, b = back[step]
            spans.append((a, b))
    return list(reversed(spans))
