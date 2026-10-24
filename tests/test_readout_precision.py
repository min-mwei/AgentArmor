import json
import math

import numpy as np
import pytest

from armor.encoding import Row
from armor.readout import answer
from armor.v0 import contract, toolcall_score, xpia_flagged


def served_answer(probability):
    row = Row("q", "noul", [], [], ["false", "true"], [])
    return json.loads(json.dumps(answer(row, np.array([1 - probability, probability]))))


@pytest.mark.parametrize("offset", [-0.00002, 0.00002])
def test_toolcall_serialization_preserves_cutoff_decision(offset):
    cutoffs, _ = contract()
    probability = cutoffs["toolcall"]["cutoff"] + offset
    score, cutoff = toolcall_score(
        {
            "rule_h_matches": served_answer(probability),
            "active_user_boundary": served_answer(0),
            "user_requested_exact_operation": served_answer(1),
        },
        {"rule_h_matches": "hard_deny"},
    )
    assert (score > cutoff) == (probability > cutoff)


@pytest.mark.parametrize("offset", [-0.000001, 0.000001])
def test_xpia_serialization_preserves_cutoff_decision(offset):
    cutoffs, _ = contract()
    threshold = 1 / (1 + math.exp(-cutoffs["bipia"]["cutoff"]))
    probability = threshold + offset
    result = served_answer(probability)
    assert xpia_flagged(result["noul"]) == xpia_flagged(probability)
    assert result["noul"] == result["probabilities"]["true"] == probability
