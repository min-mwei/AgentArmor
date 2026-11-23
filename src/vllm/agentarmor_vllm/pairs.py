"""The span-pair evidence readout of a Kai checkpoint: its scorer and its decoder, in NumPy.

Provenance: this arithmetic comes from the Kai project (its training package's evidence module:
the own-question-memory pair scorer and the disjoint-interval decoder) and is published here by
that project's owner so that a served Kai marks evidence with the numerical program its training
reads ran. Only the mathematics is here: no weights, no data, no loss and no training code. The
scorer is that module's, operation for operation, written with NumPy in float32 where the
original is MLX in float32; the decoder was NumPy there and is unchanged but for its layout.

A package whose evidence names this readout pins the SHA-256 of this file: change a byte and
such a package is refused before anything is loaded.

What it computes, for one question's row:

- the state's raw hidden states (the state marker first) are projected by ``k``, and the
  question's deciding state and its own branch (the question's tokens through the deciding
  one) by ``q``; each is normalized over its width with no learned scale;
- every state token attends once over the question's own branch, and a small network of the
  token, what it attended to and their product gives a start and an end vector for it, turned
  by position (rotary), so a pair's score depends on how far apart its ends are;
- score[i, j] says tokens i..j are one piece of evidence; a per-question bias is added. The
  state marker (row 0) is never an end of a span;
- the decoder keeps the pairs that score above the package's threshold, widens each to its
  tokens' characters without the white space at its ends, and returns the at most ``max_spans``
  disjoint intervals of greatest total score, as half-open code-point intervals of the state.

Nothing here says a span is right. No head is qualified by being readable.

NumPy and the standard library only.
"""

import math
from numbers import Real

import numpy as np

MAX_STATE_TOKENS = 4096  # with the one state marker at position zero
MAX_BRANCH_TOKENS = 1024
MAX_SPANS = 32
TENSORS = ("k", "q", "pair_memory.hidden", "pair_memory.start", "pair_memory.end")
EPS = np.float32(1e-5)


def _linear(w, x):
    return x @ w["weight"].T + w["bias"]


def _norm(x):
    """Layer normalization over the width, without a learned scale or shift."""
    centered = x - x.mean(axis=-1, keepdims=True)
    return centered / np.sqrt(x.var(axis=-1, keepdims=True) + EPS)


def _softmax(x):
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def _silu(x):
    with np.errstate(over="ignore"):  # exp of a large number is infinite, and the quotient 0
        return x / (np.float32(1) + np.exp(-x))


def _rope(x):
    length, dim = x.shape
    rate = np.float32(10000.0) ** (np.float32(-2) * np.arange(dim // 2, dtype=np.float32) / dim)
    angle = np.arange(length, dtype=np.float32)[:, None] * rate
    a, b = x[:, 0::2], x[:, 1::2]
    cos, sin = np.cos(angle), np.sin(angle)
    return np.stack([a * cos - b * sin, a * sin + b * cos], axis=-1).reshape(x.shape)


def layout(tensors, hidden_size):
    """(projection width, pair width) of a head file's tensors, or ValueError: exactly the
    scorer's twelve float32 tensors, each of the shape the others imply."""
    names = {
        f"{part}.{leaf}" for part in (*TENSORS, "pair_memory.bias") for leaf in ("weight", "bias")
    }
    if set(tensors) != names:
        raise ValueError("evidence head tensors are not the pair scorer's")
    if any(np.asarray(t).dtype != np.float32 for t in tensors.values()):
        raise ValueError("evidence head tensors must be float32")
    shape = {name: tuple(np.asarray(t).shape) for name, t in tensors.items()}
    dim, width = shape["k.bias"][0], shape["pair_memory.hidden.bias"][0]
    wanted = {
        "k.weight": (dim, hidden_size),
        "k.bias": (dim,),
        "q.weight": (dim, hidden_size),
        "q.bias": (dim,),
        "pair_memory.hidden.weight": (width, 3 * dim),
        "pair_memory.hidden.bias": (width,),
        "pair_memory.start.weight": (width, width),
        "pair_memory.start.bias": (width,),
        "pair_memory.end.weight": (width, width),
        "pair_memory.end.bias": (width,),
        "pair_memory.bias.weight": (1, dim),
        "pair_memory.bias.bias": (1,),
    }
    if shape != wanted or dim < 1 or width < 2 or width % 2:
        raise ValueError("evidence head tensor shapes are not the pair scorer's")
    if not all(np.isfinite(np.asarray(t)).all() for t in tensors.values()):
        raise ValueError("nonfinite evidence head")
    return dim, width


def pair_scores(tensors, state, decide, own_branch):
    """float32 [n, n] pair scores for a state of n rows (the marker first), the question's
    deciding state and its own branch's states. Raw hidden states in; nothing is clipped."""
    part = {
        name: {leaf: np.asarray(tensors[f"{name}.{leaf}"]) for leaf in ("weight", "bias")}
        for name in (*TENSORS, "pair_memory.bias")
    }
    hidden = part["k"]["weight"].shape[1]
    state, decide, own_branch = (
        np.asarray(x, dtype=np.float32) for x in (state, decide, own_branch)
    )
    if state.ndim != 2 or not 1 <= state.shape[0] <= MAX_STATE_TOKENS or state.shape[1] != hidden:
        raise ValueError("state features exceed the registered shape or token budget")
    if decide.shape != (hidden,):
        raise ValueError("exactly one decide feature is required")
    if (
        own_branch.ndim != 2
        or not 1 <= own_branch.shape[0] <= MAX_BRANCH_TOKENS
        or own_branch.shape[1] != hidden
    ):
        raise ValueError("own-branch features exceed the registered shape or token budget")
    keys = _linear(part["k"], state)
    query, memory = _linear(part["q"], decide), _linear(part["q"], own_branch)
    dim = part["pair_memory.hidden"]["weight"].shape[1] // 3
    k, q, m = _norm(keys), _norm(query), _norm(memory)
    attention = _softmax((k @ m.T) / np.float32(math.sqrt(dim)))
    context = attention @ m
    u = _silu(
        _linear(part["pair_memory.hidden"], np.concatenate([k, context, k * context], axis=-1))
    )
    start = _rope(_linear(part["pair_memory.start"], u))
    end = _rope(_linear(part["pair_memory.end"], u))
    bias = _linear(part["pair_memory.bias"], q).reshape(())
    scores = (start @ end.T) / np.float32(math.sqrt(start.shape[-1])) + bias
    return scores.astype(np.float32, copy=False)


def _valid(n):
    """Which pairs may be a span: from a row to itself or a later one, never the marker."""
    i = np.arange(n)
    return (i[:, None] >= 1) & (i[:, None] <= i[None, :])


def _character_candidates(scores, offsets, state, threshold):
    """Pairs scoring above the threshold, as the character hulls of their tokens."""
    n, length = len(offsets), len(state)
    valid = _valid(n)
    if not np.isfinite(scores[valid]).all():
        raise ValueError("nonfinite valid pair score")
    i, j = np.nonzero(valid & (scores > threshold))
    weights = scores[i, j] - threshold
    if not len(i):
        return np.array([], dtype=int), np.array([], dtype=int), weights
    starts, ends = np.asarray(offsets, dtype=int).T
    real = ends > starts
    starts, ends = np.where(real, starts, length + 1), np.where(real, ends, -1)
    a, b = np.empty(len(i), dtype=int), np.empty(len(i), dtype=int)
    edges = np.r_[0, np.flatnonzero(np.diff(i)) + 1, len(i)]
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        first = i[lo]
        a[lo:hi] = np.minimum.accumulate(starts[first:])[j[lo:hi] - first]
        b[lo:hi] = np.maximum.accumulate(ends[first:])[j[lo:hi] - first]
    keep = (a >= 0) & (a < b) & (b <= length)
    a, b, weights = a[keep], b[keep], weights[keep]
    left, right = np.arange(length + 1), np.arange(length + 1)
    for p in range(length - 1, -1, -1):
        if state[p].isspace():
            left[p] = left[p + 1]
    for p in range(1, length + 1):
        if state[p - 1].isspace():
            right[p] = right[p - 1]
    a, b = left[a], right[b]
    keep = a < b
    return a[keep], b[keep], weights[keep]


def decode_pairs(scores, offsets, state, *, threshold=0.0, max_spans=MAX_SPANS):
    """The at most max_spans disjoint intervals of greatest total score above the threshold,
    as [[start, end]] code-point intervals of state in order.

    Adjacent intervals are allowed. Equal totals keep the solution that ends earlier; equal
    candidates keep token-pair order. offsets are the state rows' places, the marker's (0, 0)
    first.
    """
    if not isinstance(state, str) or not offsets or tuple(offsets[0]) != (0, 0):
        raise ValueError("a string state and one leading empty state marker are required")
    if not 1 <= len(offsets) <= MAX_STATE_TOKENS:
        raise ValueError("pair state length exceeds the registered budget")
    if type(max_spans) is not int or not 1 <= max_spans <= MAX_SPANS:
        raise ValueError("decoded span count exceeds the registered budget")
    if (
        not isinstance(threshold, Real)
        or isinstance(threshold, bool)
        or not math.isfinite(threshold)
    ):
        raise ValueError("a finite pair threshold is required")
    for pair in offsets:
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or any(type(p) is not int for p in pair)
            or not 0 <= pair[0] <= pair[1] <= len(state)
        ):
            raise ValueError("token offset is outside the original state")
    scores = np.asarray(scores, dtype=np.float64)
    if scores.shape != (len(offsets), len(offsets)):
        raise ValueError("pair scores and original offsets differ in shape")
    starts, ends, weights = _character_candidates(scores, offsets, state, threshold)
    if not len(weights):
        return []
    order = np.argsort(ends, kind="stable")
    starts, ends, weights = starts[order], ends[order], weights[order]
    boundaries = np.unique(np.r_[0, ends])
    previous = np.searchsorted(boundaries, starts, side="right") - 1
    best = np.zeros((max_spans + 1, len(boundaries)), dtype=np.float64)
    back = [[None] * len(boundaries) for _ in range(max_spans + 1)]
    for step, end in enumerate(boundaries[1:], start=1):
        lo = np.searchsorted(ends, end, side="left")
        hi = np.searchsorted(ends, end, side="right")
        values = best[:-1, previous[lo:hi]] + weights[None, lo:hi]
        choices = np.argmax(values, axis=1)
        best[:, step] = best[:, step - 1]
        for count, choice in enumerate(choices, start=1):
            index = lo + choice
            if values[count - 1, choice] > best[count, step]:
                best[count, step] = values[count - 1, choice]
                back[count][step] = (int(previous[index]), int(starts[index]), int(ends[index]))
    spans, count, step = [], max_spans, len(boundaries) - 1
    while step:
        if back[count][step] is None:
            step -= 1
        else:
            step, a, b = back[count][step]
            spans.append([a, b])
            count -= 1
    return list(reversed(spans))


class PairReadout:
    """The readout engine.EVIDENCE runs for a package that names it: made from the package's
    evidence head file and the decoder settings the package states (its threshold and span
    budget; this file chooses neither)."""

    name = "joint_pairs_memory_v1"

    def __init__(self, tensors, package):
        told = package.evidence
        threshold, budget = told.get("threshold"), told.get("max_spans")
        if (
            not isinstance(threshold, Real)
            or isinstance(threshold, bool)
            or not math.isfinite(threshold)
            or type(budget) is not int
            or not 1 <= budget <= MAX_SPANS
        ):
            raise ValueError("the package states no decoder threshold and span budget")
        self.tensors = {name: np.asarray(t) for name, t in tensors.items()}
        self.dim, self.width = layout(self.tensors, package.hidden_size)
        self.threshold, self.max_spans = float(threshold), budget

    def scores(self, hidden, row, state_rows):
        """The question's pair scores from its whole row's raw states: the first state_rows
        are the state, the rest the question's own branch, the deciding state its last."""
        hidden = np.asarray(hidden)
        if row["decide"] != len(hidden) - 1 or not 1 <= state_rows <= row["decide"]:
            raise ValueError("the row is not a state and one question's branch")
        return pair_scores(
            self.tensors, hidden[:state_rows], hidden[row["decide"]], hidden[state_rows:]
        )

    def spans(self, hidden, row, offsets, state):
        offsets = [tuple(pair) for pair in offsets]
        scores = self.scores(hidden, row, len(offsets))
        return decode_pairs(
            scores, offsets, state, threshold=self.threshold, max_spans=self.max_spans
        )
