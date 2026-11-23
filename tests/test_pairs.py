"""The published span-pair readout (agentarmor_vllm.pairs): its arithmetic against an
independent reference and, where Kai's MLX module can be imported, against that; its decoder
on scores written by hand; its refusals; and that a head beside an answer changes no answer.

No trained head is used: tensors here are random. Nothing says a span is right."""

import importlib.util
import math
import os
from types import SimpleNamespace

import numpy as np
import pytest
from agentarmor_vllm import engine, pairs
from agentarmor_vllm.encode import SPECIAL, tokenizer_sha256
from agentarmor_vllm.engine import Pointer, Service

HIDDEN, DIM, WIDTH = 12, 6, 4


def head(seed=0, hidden=HIDDEN, dim=DIM, width=WIDTH, scale=0.5):
    rng = np.random.default_rng(seed)

    def made(*shape):
        return (rng.standard_normal(shape) * scale).astype(np.float32)

    return {
        "k.weight": made(dim, hidden),
        "k.bias": made(dim),
        "q.weight": made(dim, hidden),
        "q.bias": made(dim),
        "pair_memory.hidden.weight": made(width, 3 * dim),
        "pair_memory.hidden.bias": made(width),
        "pair_memory.start.weight": made(width, width),
        "pair_memory.start.bias": made(width),
        "pair_memory.end.weight": made(width, width),
        "pair_memory.end.bias": made(width),
        "pair_memory.bias.weight": made(1, dim),
        "pair_memory.bias.bias": made(1),
    }


def features(seed, n, branch, hidden=HIDDEN):
    rng = np.random.default_rng(seed)
    return (
        rng.standard_normal((n, hidden)).astype(np.float32),
        rng.standard_normal(hidden).astype(np.float32),
        rng.standard_normal((branch, hidden)).astype(np.float32),
    )


def reference(t, state, decide, branch):
    """The same scores, one number at a time in float64, written from the description."""
    t = {name: np.asarray(v, dtype=np.float64) for name, v in t.items()}

    def lin(name, x):
        return t[name + ".weight"] @ x + t[name + ".bias"]

    def norm(x):
        mean = sum(x) / len(x)
        var = sum((v - mean) ** 2 for v in x) / len(x)
        return np.array([(v - mean) / math.sqrt(var + 1e-5) for v in x])

    def turned(x, position):
        out = []
        for d in range(len(x) // 2):
            angle = position * 10000.0 ** (-2 * d / len(x))
            a, b = x[2 * d], x[2 * d + 1]
            out += [
                a * math.cos(angle) - b * math.sin(angle),
                a * math.sin(angle) + b * math.cos(angle),
            ]
        return np.array(out)

    memory = [norm(lin("q", row)) for row in np.asarray(branch, dtype=np.float64)]
    query = norm(lin("q", np.asarray(decide, dtype=np.float64)))
    bias = float(lin("pair_memory.bias", query)[0])
    starts, ends = [], []
    for position, row in enumerate(np.asarray(state, dtype=np.float64)):
        k = norm(lin("k", row))
        weights = np.array([math.exp(float(k @ m) / math.sqrt(len(k))) for m in memory])
        context = sum(w * m for w, m in zip(weights / weights.sum(), memory, strict=True))
        u = lin("pair_memory.hidden", np.concatenate([k, context, k * context]))
        u = u / (1 + np.exp(-u))
        starts.append(turned(lin("pair_memory.start", u), position))
        ends.append(turned(lin("pair_memory.end", u), position))
    width = len(starts[0])
    return np.array([[float(s @ e) / math.sqrt(width) + bias for e in ends] for s in starts])


@pytest.mark.parametrize(("seed", "n", "branch"), [(0, 1, 1), (1, 7, 3), (2, 40, 9), (3, 130, 31)])
def test_the_scorer_is_the_described_arithmetic(seed, n, branch):
    tensors, (state, decide, own) = head(seed), features(seed + 10, n, branch)
    scores = pairs.pair_scores(tensors, state, decide, own)
    assert scores.shape == (n, n) and scores.dtype == np.float32
    assert np.allclose(scores, reference(tensors, state, decide, own), atol=2e-4, rtol=2e-4)


def test_a_score_depends_on_its_own_question_only():
    tensors, (state, decide, own) = head(4), features(5, 20, 6)
    _, other_decide, other_branch = features(6, 20, 11)
    first = pairs.pair_scores(tensors, state, decide, own)
    assert np.array_equal(first, pairs.pair_scores(tensors, state, decide, own))
    assert not np.allclose(first, pairs.pair_scores(tensors, state, other_decide, other_branch))


# --- the decoder on scores written by hand -------------------------------------------------------

STATE = "ab  cd ef\ngh"
PLACES = [(0, 0), (0, 2), (2, 3), (4, 6), (7, 9), (10, 12)]  # the marker, then five tokens


def scored(**marked):
    scores = np.full((len(PLACES), len(PLACES)), -5.0)
    for name, value in marked.items():
        i, j = (int(x) for x in name[1:].split("_"))
        scores[i, j] = value
    return scores


@pytest.mark.parametrize(
    ("scores", "settings", "spans"),
    [
        (scored(), {}, []),
        (scored(p1_1=2.0), {}, [[0, 2]]),
        (scored(p1_3=2.0), {}, [[0, 6]]),  # a hull of three tokens, white space inside kept
        (scored(p2_2=2.0), {}, []),  # a token of white space alone is no evidence
        (scored(p2_3=2.0), {}, [[4, 6]]),  # white space at an end is left out
        (scored(p1_1=1.0, p3_3=1.0, p1_3=1.5), {}, [[0, 2], [4, 6]]),  # two beat the one over both
        (scored(p1_1=1.0, p3_3=1.0, p1_3=2.5), {}, [[0, 6]]),
        (scored(p1_1=1.0, p3_3=3.0, p5_5=2.0), {"max_spans": 2}, [[4, 6], [10, 12]]),
        (scored(p1_1=1.0, p3_3=3.0), {"threshold": 1.5}, [[4, 6]]),
        (scored(p0_0=9.0, p0_3=9.0), {}, []),  # the marker is never an end of a span
        (scored(p4_5=1.0), {}, [[7, 12]]),  # a line break inside a hull is kept
        (scored(p3_1=9.0), {}, []),  # an end before its start is not a pair
    ],
)
def test_the_decoder_keeps_the_best_disjoint_intervals(scores, settings, spans):
    assert pairs.decode_pairs(scores, PLACES, STATE, **settings) == spans


@pytest.mark.parametrize(
    "call",
    [
        lambda: pairs.decode_pairs(scored(), PLACES[1:], STATE),  # no marker
        lambda: pairs.decode_pairs(scored()[:3, :3], PLACES, STATE),
        lambda: pairs.decode_pairs(scored(), PLACES, STATE, max_spans=33),
        lambda: pairs.decode_pairs(scored(), PLACES, STATE, threshold=math.inf),
        lambda: pairs.decode_pairs(scored(), [*PLACES[:-1], (10, 13)], STATE),
        lambda: pairs.decode_pairs(scored(p1_1=math.nan), PLACES, STATE),
        lambda: pairs.decode_pairs(np.zeros((4098, 1)), [(0, 0)] * 4098, STATE),
    ],
)
def test_the_decoder_refuses_what_is_not_a_state_and_its_scores(call):
    with pytest.raises(ValueError):
        call()


# --- limits and the head's layout ----------------------------------------------------------------


@pytest.mark.parametrize("n", [2048, 2049, 4096])
def test_a_state_up_to_4096_rows_is_scored_and_decoded(n):
    tensors, (state, decide, own) = head(7, scale=0.2), features(8, n, 5)
    scores = pairs.pair_scores(tensors, state, decide, own)
    assert scores.shape == (n, n) and np.isfinite(scores).all()
    text = "x" * (n - 1)
    places = [(0, 0), *((i, i + 1) for i in range(n - 1))]
    threshold = float(np.sort(scores[pairs._valid(n)])[-40])  # about forty pairs above it
    spans = pairs.decode_pairs(scores, places, text, threshold=threshold)
    assert 1 <= len(spans) <= 32 and engine.checked_spans(spans, len(text)) == spans


def test_more_rows_than_the_limit_are_refused():
    tensors, (state, decide, own) = head(7), features(8, 4097, 5)
    with pytest.raises(ValueError):
        pairs.pair_scores(tensors, state, decide, own)
    with pytest.raises(ValueError):
        pairs.pair_scores(tensors, state[:10], decide, features(9, 1, 1025)[2])


def about(threshold=0.0, max_spans=32, hidden=HIDDEN, **more):
    told = {"readout": pairs.PairReadout.name, "threshold": threshold, "max_spans": max_spans}
    return SimpleNamespace(
        evidence={k: v for k, v in told.items() if v is not None}, hidden_size=hidden, **more
    )


def renamed(t):
    return {("k2.weight" if name == "k.weight" else name): v for name, v in t.items()}


@pytest.mark.parametrize(
    ("tensors", "package"),
    [
        (renamed(head()), about()),
        ({k: v for k, v in head().items() if k != "q.bias"}, about()),
        (head() | {"extra.weight": np.zeros((1, 1), dtype=np.float32)}, about()),
        (head() | {"k.bias": head()["k.bias"].astype(np.float64)}, about()),
        (head() | {"k.bias": np.full(DIM, np.nan, dtype=np.float32)}, about()),
        (head(), about(hidden=HIDDEN + 1)),
        (head(width=3), about()),
        (head() | {"pair_memory.end.bias": np.zeros(WIDTH + 2, dtype=np.float32)}, about()),
        (head(), about(threshold=None)),
        (head(), about(max_spans=None)),
        (head(), about(max_spans=33)),
        (head(), about(threshold=math.nan)),
        (head(), about(threshold=True)),
    ],
)
def test_a_head_that_is_not_the_scorers_or_states_no_decoder_is_refused(tensors, package):
    with pytest.raises(ValueError):
        pairs.PairReadout(tensors, package)


def test_the_published_readout_is_the_only_one_registered():
    assert engine.EVIDENCE == {"joint_pairs_memory_v1": pairs.PairReadout}


# --- beside an answer ----------------------------------------------------------------------------


class Tokenizer:
    unk_token_id = -1
    backend_tokenizer = SimpleNamespace(to_str=lambda: "synthetic-tokenizer/v1")

    def convert_tokens_to_ids(self, text):
        return SPECIAL.index(text) + 1

    def __call__(self, text, **_):
        places = [(i, i + 1) for i in range(len(text))]
        return SimpleNamespace(input_ids=[ord(c) + 10 for c in text], offset_mapping=places)


class Backbone:
    name = "mlx-lora"

    def hidden(self, row):
        rng = np.random.default_rng(len(row["ids"]))
        return rng.standard_normal((len(row["ids"]), HIDDEN)).astype(np.float32)


def service(threshold=None):
    tok = Tokenizer()
    meta = {"tokenizer_sha256": tokenizer_sha256(tok), "pauses": 0, "pause_id": None}
    meta |= {"max_state": 4096, "max_row": 6000, "max_packed": 9000, "temperature": 1.0}
    package = about(threshold, meta=meta, identity="a" * 64)
    rng = np.random.default_rng(1)
    pointer = Pointer(
        {
            "q.weight": rng.standard_normal((3, HIDDEN)),
            "q.bias": rng.standard_normal(3),
            "k.weight": rng.standard_normal((3, HIDDEN)),
            "k.bias": rng.standard_normal(3),
        },
        HIDDEN,
    )
    made = Service(package, tok, pointer, Backbone())
    if threshold is not None:
        made.evidence, made.head_sha256 = pairs.PairReadout(head(11), package), "c" * 64
    return made


ASK = {
    "state": "the user said: send it to the archive, not the mirror",
    "questions": {
        "a": {"type": "noul", "instructions": "is this an attempt"},
        "b": {
            "type": "choice",
            "instructions": "decide",
            "criteria": {"allow": "ok", "deny": "no"},
        },
    },
}


def test_an_answer_is_bit_for_bit_the_same_with_the_head_and_without():
    without, marking, silent = service(), service(-50.0), service(1e9)
    plain, marked, quiet = (s.evaluate(ASK) for s in (without, marking, silent))
    for name in ASK["questions"]:
        beside = {k: v for k, v in marked["answers"][name].items() if k != "evidence"}
        assert beside == plain["answers"][name]  # the same floats, not nearly the same
        assert marked["answers"][name]["evidence"]["status"] == "spans"
        assert quiet["answers"][name]["evidence"] == {"status": "empty", "spans": []}
        spans = marked["answers"][name]["evidence"]["spans"]
        assert engine.checked_spans(spans, len(ASK["state"])) == spans
    assert "evidence" not in plain and plain["evidence_available"] is False
    assert marked["evidence"]["readout"] == "joint_pairs_memory_v1"
    assert marked["evidence"]["qualified"] is False and marked["policy_ready"] is False


# --- against Kai's own module, where it can be imported -----------------------------------------


@pytest.fixture(scope="module")
def kai_evidence():
    """Kai's evidence module, from the file KAI_EVIDENCE_SOURCE names. It needs MLX; without
    either this comparison is skipped and the reference above is what stands."""
    source = os.environ.get("KAI_EVIDENCE_SOURCE")
    if not source:
        pytest.skip("KAI_EVIDENCE_SOURCE is not set")
    mx = pytest.importorskip("mlx.core")
    spec = importlib.util.spec_from_file_location("kai_evidence_source", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MAX_STATE_TOKENS = pairs.MAX_STATE_TOKENS  # as Kai's own span code raises it
    # Float32 as written: MLX on the CPU. On Apple GPUs MLX by default runs float32 matrix
    # products at reduced precision (MLX_ENABLE_TF32 unset), which is a property of that
    # device's arithmetic and not of this readout; the last test below measures it.
    device = mx.default_device()
    mx.set_default_device(mx.cpu)
    yield module
    mx.set_default_device(device)


@pytest.mark.parametrize(
    ("seed", "n", "branch"),
    [(0, 5, 3), (1, 300, 40), (2, 2048, 64), (3, 2049, 64)] + [(4, 4096, 200)],
)
def test_scores_and_spans_are_those_of_kais_module(kai_evidence, capsys, seed, n, branch):
    import mlx.core as mx
    from mlx.utils import tree_flatten, tree_unflatten

    hidden, dim, width = 64, 32, 16
    tensors = head(seed, hidden, dim, width, scale=0.3)
    state, decide, own = features(seed + 20, n, branch, hidden)
    theirs = kai_evidence.MemoryPairEvidenceHead(hidden, dim, width)
    assert {name for name, _ in tree_flatten(theirs.parameters())} == set(tensors)
    theirs.update(tree_unflatten([(name, mx.array(value)) for name, value in tensors.items()]))
    expected = np.array(
        theirs(mx.array(state), mx.array(decide), mx.array(own), detach_features=True)
    )
    scores = pairs.pair_scores(tensors, state, decide, own)
    difference = float(np.abs(scores - expected).max())
    # Operation for operation in float32. At thousands of rows the rotary angle (row times a
    # rate) is itself only good to a few parts in ten thousand in float32, and the two
    # libraries' powers differ in the last place of the rate: that, not the scorer, is the
    # distance seen at 2,048 rows and more.
    with capsys.disabled():
        print(f"\nrows {n}: largest pair-score difference from Kai's module {difference:.6f}")
    assert difference < (2e-4 if n <= 300 else 2e-3), difference
    text = "".join("ab c"[i % 4] for i in range(n - 1))
    places = [(0, 0), *((i, i + 1) for i in range(n - 1))]
    threshold = float(np.sort(expected[pairs._valid(n)])[-min(60, n)]) if n > 1 else 0.0
    for budget in (1, 5, 32):
        ours = pairs.decode_pairs(expected, places, text, threshold=threshold, max_spans=budget)
        assert ours == kai_evidence.decode_pairs(
            expected, places, text, threshold=threshold, max_spans=budget
        )
    # From its own scores the port marks the same spans unless a pair sits on the threshold.
    margin = np.abs(expected[pairs._valid(n)] - threshold).min()
    if margin > 2 * difference:
        assert pairs.decode_pairs(
            scores, places, text, threshold=threshold
        ) == kai_evidence.decode_pairs(expected, places, text, threshold=threshold)


def test_the_gpu_default_arithmetic_is_not_float32_and_by_how_much(kai_evidence, capsys):
    """Not a check of this readout: a record of how far MLX's default GPU arithmetic is from
    float32 on the same scorer, so that nobody reads that distance as a porting error."""
    import mlx.core as mx
    from mlx.utils import tree_unflatten

    if not mx.metal.is_available():
        pytest.skip("no Apple GPU")
    hidden, dim, width = 64, 32, 16
    tensors = head(1, hidden, dim, width, scale=0.3)
    state, decide, own = features(21, 300, 40, hidden)
    theirs = kai_evidence.MemoryPairEvidenceHead(hidden, dim, width)
    theirs.update(tree_unflatten([(name, mx.array(value)) for name, value in tensors.items()]))
    with mx.stream(mx.gpu):
        on_gpu = np.array(
            theirs(mx.array(state), mx.array(decide), mx.array(own), detach_features=True)
        )
    distance = float(np.abs(on_gpu - pairs.pair_scores(tensors, state, decide, own)).max())
    with capsys.disabled():
        print(f"\nMLX GPU default against float32, largest pair-score difference: {distance:.5f}")
    assert np.isfinite(distance)
