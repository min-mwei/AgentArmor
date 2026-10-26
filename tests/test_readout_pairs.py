import json
from pathlib import Path

import numpy as np
import pytest

from armor.encoding import Row
from armor.readout import load_evidence
from armor.readout_pairs import PairEvidenceReadout, decode_pairs


def synthetic(memory):
    rng = np.random.default_rng(17)
    prefix = "pair_memory" if memory else "pair"
    shapes = {
        "k": (4, 6),
        "q": (4, 6),
        f"{prefix}.hidden": (4, 12),
        f"{prefix}.start": (4, 4),
        f"{prefix}.end": (4, 4),
        f"{prefix}.bias": (1, 4),
    }
    weights = {}
    for name, shape in shapes.items():
        weights[name + ".weight"] = rng.normal(0, 0.2, shape).astype(np.float32)
        weights[name + ".bias"] = rng.normal(0, 0.2, shape[0]).astype(np.float32)
    hidden = rng.normal(size=(9, 6)).astype(np.float32)
    offsets = [(0, 0), (0, 2), (2, 3), (3, 4), (4, 5), (5, 7)]
    row = Row("q", "noul", list(range(9)), [7, 8], ["false", "true"], offsets)
    return weights, hidden, row


@pytest.mark.parametrize("memory", [False, True])
def test_readout_matches_saved_kai_cpu_reference(memory):
    weights, hidden, row = synthetic(memory)
    expected = json.loads(
        (Path(__file__).parent / "fixtures/pair_readout_reference.json").read_text()
    )
    reference = expected["memory" if memory else "pair"]
    readout = PairEvidenceReadout(weights, 6, memory)
    np.testing.assert_allclose(
        readout.scores(hidden, row), reference["scores"], atol=2e-6, rtol=2e-6
    )
    assert readout.spans(hidden, row, " a🙂b c ") == [tuple(x) for x in reference["spans"]]


def test_decoder_prefers_two_complete_parts_over_one_weaker_overlapping_span():
    z = np.zeros((5, 5))
    z[1, 2], z[3, 4], z[1, 4] = 2, 3, 4
    assert decode_pairs(z, [(0, 0), (0, 1), (1, 2), (3, 4), (4, 5)], "ab cd") == [(0, 2), (3, 5)]
    z[1, 2] = np.nan
    with pytest.raises(ValueError, match="Nonfinite"):
        decode_pairs(z, [(0, 0), (0, 1), (1, 2), (3, 4), (4, 5)], "ab cd")


def test_pair_head_metadata_requires_its_sidecar_and_exact_shapes(tmp_path):
    meta = {"args": {"evidence_objective": "pairs", "evidence_readout": "joint_pairs_memory"}}
    with pytest.raises(ValueError, match="sidecar"):
        load_evidence(tmp_path, meta, 6)
    weights, _, _ = synthetic(True)
    weights["pair_memory.hidden.weight"] = np.zeros((4, 11))
    with pytest.raises(ValueError, match="tensor"):
        PairEvidenceReadout(weights, 6, True)
