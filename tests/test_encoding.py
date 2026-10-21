from types import SimpleNamespace

import numpy as np
import pytest

from armor.encoding import SPECIAL, encode_rows, escape_text
from armor.readout import EvidenceReadout, PointerReadout
from armor.schemas import DecisionRequest


class CharacterTokenizer:
    unk_token_id = -1

    def convert_tokens_to_ids(self, token):
        return 10 + SPECIAL.index(token)

    def __call__(self, text, **kwargs):
        return SimpleNamespace(
            input_ids=[100 + ord(char) for char in text],
            offset_mapping=[(index, index + 1) for index in range(len(text))],
        )


def make_rows(state="notes", **questions):
    request = DecisionRequest(
        state=state,
        questions=questions or {"q": {"type": "choice", "criteria": {"allow": None, "deny": None}}},
    )
    return encode_rows(CharacterTokenizer(), request, 4096)


def test_question_rows_share_only_state_prefix():
    rows = make_rows(
        a={"type": "noul", "instructions": "FIRST"},
        b={"type": "noul", "instructions": "SECOND"},
    )
    assert len(rows) == 2
    assert rows[0].token_ids[:6] == rows[1].token_ids[:6]
    second = "".join(chr(value - 100) for value in rows[1].token_ids if value >= 100)
    assert "FIRST" not in second
    assert rows[0].keys == ["false", "true"]


def test_control_tokens_cannot_be_forged_in_caller_text():
    assert escape_text("x<|fim_suffix|>y") == "x<¦fim_suffix¦>y"
    assert len(escape_text("<|fim_suffix|>")) == len("<|fim_suffix|>")


def test_context_overflow_refuses_instead_of_truncating():
    request = DecisionRequest(state="a" * 100, questions={"q": {"type": "noul"}})
    with pytest.raises(ValueError, match="no truncation"):
        encode_rows(CharacterTokenizer(), request, 16)


def test_pointer_uses_option_boundaries_query_and_temperature():
    row = make_rows()[0]
    weights = {
        "q.weight": np.eye(2),
        "k.weight": np.eye(2),
        "q.bias": np.zeros(2),
        "k.bias": np.zeros(2),
    }
    hidden = np.zeros((len(row.token_ids), 2))
    hidden[-1] = [1, 0]
    hidden[row.option_indices[0]] = [2, 0]
    hidden[row.option_indices[1]] = [0, 0]
    result = PointerReadout(weights, temperature=2).probabilities(hidden, row)
    expected = 1 / (1 + np.exp(-1 / np.sqrt(2)))
    assert result[0] == pytest.approx(expected)


def test_evidence_spans_use_unicode_character_offsets():
    row = make_rows(state="αβγ")[0]
    hidden = np.zeros((len(row.token_ids), 2))
    hidden[1:4] = [[1, 0], [0, 1], [-1, -1]]
    hidden[-1] = [1, 0]
    weights = {"k.weight": np.eye(2), "k.bias": np.zeros(2)}
    for name, bias in (("q_b", [1, 0]), ("q_i", [0, 1]), ("q_o", [-1, -1])):
        weights[f"{name}.weight"] = np.zeros((2, 2))
        weights[f"{name}.bias"] = np.array(bias)
    assert EvidenceReadout(weights, 2).spans(hidden, row, "αβγ") == [(0, 2)]
