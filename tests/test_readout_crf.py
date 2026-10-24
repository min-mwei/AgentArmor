import itertools
from types import SimpleNamespace

import numpy as np
import pytest

from armor import readout


def weights(crf=False):
    result = {"k.weight": np.eye(3), "k.bias": np.zeros(3)}
    for i, name in enumerate(("q_b", "q_i", "q_o")):
        result[f"{name}.weight"] = np.zeros((3, 3))
        result[f"{name}.bias"] = np.eye(3)[i]
    if crf:
        result["transitions"] = np.zeros((3, 3))
    return result


def head(transitions=None):
    w = weights(True)
    if transitions is not None:
        w["transitions"] = transitions
    return readout.EvidenceReadout(w, 3, objective="crf")


def decode(z, transitions=None, state="α🙂 e\u0301Z"):
    row = SimpleNamespace(offsets=[(0, 0), *[(i, i + 1) for i in range(len(state))]])
    hidden = np.vstack([z * np.sqrt(3), np.zeros(3)])
    return head(transitions).spans(hidden, row, state)


@pytest.mark.parametrize("length", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("ties", [False, True])
def test_path_matches_independent_complete_enumeration(length, ties):
    rng = np.random.default_rng(length)
    if ties:
        z, a = rng.integers(-1, 2, (length, 3)), rng.integers(-1, 2, (3, 3))
    else:
        z, a = rng.normal(size=(length, 3)), rng.normal(size=(3, 3))
    path = readout.bio_viterbi(z, a)
    paths = [[2, *p] for p in itertools.product(range(3), repeat=length - 1)]
    paths = [p for p in paths if all(x != 2 or y != 1 for x, y in zip(p[:-1], p[1:], strict=True))]

    def score(p):
        return sum(z[t, p[t]] + a[p[t - 1], p[t]] for t in range(1, length))

    assert score(path) == pytest.approx(max(map(score, paths)), abs=1e-12)
    if ties:
        assert path == min(paths, key=lambda p: (-score(p), tuple(reversed(p[1:]))))
    assert path[0] == 2


def test_ties_marker_empty_adjacent_multipart_and_unicode():
    assert readout.bio_viterbi(np.zeros((4, 3)), np.zeros((3, 3))) == [2, 0, 0, 0]
    assert decode(np.zeros((1, 3)), state="") == []
    z = np.full((7, 3), -30.0)
    z[np.arange(7), [2, 0, 0, 2, 0, 1, 2]] = 30
    assert decode(z) == [(0, 1), (1, 2), (3, 5)]
    z[0] = [1e6, -1e6, 0]
    assert decode(z) == [(0, 1), (1, 2), (3, 5)]


def test_scaling_changes_crf_competition_with_transition_scores():
    w = weights(True)
    w["transitions"][2, 2] = 1.5
    h = readout.EvidenceReadout(w, 3, objective="crf")
    row = SimpleNamespace(offsets=[(0, 0), (0, 1)])
    assert h.spans(np.array([[0, 0, 0], [2, 0, 0], [0, 0, 0]]), row, "x") == []


@pytest.mark.parametrize("objective", [None, False, "other"])
def test_invalid_objective_refused(objective):
    with pytest.raises(ValueError, match="objective"):
        readout.EvidenceReadout(weights(), 3, objective=objective)


@pytest.mark.parametrize("change", ["missing", "extra", "shape", "finite", "emission"])
def test_exact_crf_tensor_schema(change):
    w = weights(True)
    if change == "missing":
        del w["transitions"]
    elif change == "extra":
        w["extra"] = np.zeros(1)
    elif change == "shape":
        w["transitions"] = np.zeros((2, 3))
    elif change == "finite":
        w["transitions"][0, 0] = np.nan
    else:
        w["k.bias"][0] = np.inf
    with pytest.raises(ValueError):
        readout.EvidenceReadout(w, 3, objective="crf")


@pytest.mark.parametrize(
    "args", [{"evidence_objective": None}, None, [], {"evidence_objective": "other"}]
)
def test_loader_rejects_malformed_metadata_even_without_sidecar(tmp_path, args):
    with pytest.raises(ValueError, match="objective"):
        readout.load_evidence(tmp_path, {"args": args}, 3)


def test_loader_requires_metadata_and_sidecar_agreement(tmp_path):
    save_file = pytest.importorskip("safetensors.numpy").save_file
    assert readout.load_evidence(tmp_path, {}, 3) is None
    with pytest.raises(ValueError, match="sidecar"):
        readout.load_evidence(tmp_path, {"args": {"evidence_objective": "crf"}}, 3)
    path = tmp_path / "evidence_head.safetensors"
    save_file(weights(True), path)
    with pytest.raises(ValueError):
        readout.load_evidence(tmp_path, {}, 3)
    assert readout.load_evidence(tmp_path, {"args": {"evidence_objective": "crf"}}, 3)
    save_file(weights(), path)
    assert readout.load_evidence(tmp_path, {}, 3)
    with pytest.raises(ValueError):
        readout.load_evidence(tmp_path, {"args": {"evidence_objective": "crf"}}, 3)
    with pytest.raises(ValueError, match="hidden"):
        readout.load_evidence(tmp_path, {}, 4)


def test_nonfinite_runtime_emission_refused():
    with pytest.raises(ValueError, match="Nonfinite"):
        decode(np.full((1, 3), np.nan), state="")


def test_whitespace_trimming_and_unbounded_parts():
    state = " α "
    z = np.full((4, 3), -30.0)
    z[np.arange(4), [2, 0, 1, 1]] = 30
    assert decode(z, state=state) == [(1, 2)]
    z = np.tile([30.0, -30.0, -30.0], (13, 1))
    assert decode(z, state="x" * 12) == [(i, i + 1) for i in range(12)]
