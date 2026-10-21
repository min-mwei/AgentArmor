"""Kai's causal pointer layout, one isolated row per question.

The delimiter layout and state escaping follow Kai's Kev-derived core (Apache-2.0).
See NOTICE. Caller text cannot introduce structural delimiter tokens.
"""

import re
from dataclasses import dataclass
from typing import Any

from armor.schemas import ChoiceQuestion, DecisionRequest, NoulQuestion

SPECIAL = (
    "<|fim_prefix|>",
    "<|fim_middle|>",
    "<|box_start|>",
    "<|box_end|>",
    "<|fim_suffix|>",
)
CONTROL_TOKEN = re.compile(r"<\|([A-Za-z0-9_]+)\|>")


def escape_text(text: str) -> str:
    return CONTROL_TOKEN.sub(r"<¦\1¦>", text)


@dataclass
class Row:
    question_id: str
    question_type: str
    token_ids: list[int]
    option_indices: list[int]
    keys: list[str]
    offsets: list[tuple[int, int]]
    legend: dict[str, str] | None = None


def encode_rows(tokenizer: Any, request: DecisionRequest, max_model_len: int) -> list[Row]:
    token_ids = [tokenizer.convert_tokens_to_ids(token) for token in SPECIAL]
    if any(t is None or t == tokenizer.unk_token_id for t in token_ids):
        raise ValueError("The checkpoint tokenizer is missing Kai's delimiter tokens")
    if len(set(token_ids)) != len(SPECIAL):
        raise ValueError("Kai delimiter token IDs must be distinct")
    state_id, question_id, open_id, close_id, decide_id = token_ids

    def tokens(text: str) -> list[int]:
        return tokenizer(escape_text(text), add_special_tokens=False).input_ids

    state = tokenizer(
        escape_text(request.state), add_special_tokens=False, return_offsets_mapping=True
    )
    prefix = [state_id, *state.input_ids]
    if len(prefix) >= max_model_len:
        raise ValueError(
            f"State exceeds the {max_model_len}-token context; no truncation is applied"
        )
    raw_offsets = state.offset_mapping
    # Cover combining characters and gaps left by the tokenizer's normalizer.
    offsets = [(0, 0)] + [
        (start, max(end, raw_offsets[i + 1][0] if i + 1 < len(raw_offsets) else len(request.state)))
        for i, (start, end) in enumerate(raw_offsets)
    ]
    rows = []
    for qid, question in request.questions.items():
        if not qid:
            raise ValueError("Question IDs must be nonempty")
        legend = None
        if isinstance(question, ChoiceQuestion):
            keys = list(question.criteria)
            options = [
                name if not desc else f"{name}: {desc}" for name, desc in question.criteria.items()
            ]
        elif isinstance(question, NoulQuestion):
            keys = ["false", "true"]
            criteria = question.criteria or {}
            options = [
                word if not criteria.get(key) else f"{word}: {criteria[key]}"
                for key, word in zip(keys, ["no", "yes"], strict=True)
            ]
        else:
            keys = [str(i) for i in range(len(question.criteria))]
            options = question.criteria
            legend = dict(zip(keys, options, strict=True))
        ids = [*prefix, question_id, *tokens(question.instructions)]
        option_indices = []
        for option in options:
            ids.extend([open_id, *tokens(option), close_id])
            option_indices.append(len(ids) - 1)
        ids.append(decide_id)
        if len(ids) > max_model_len:
            raise ValueError(f"Question {qid!r} exceeds the {max_model_len}-token context")
        rows.append(Row(qid, question.type, ids, option_indices, keys, offsets, legend))
    return rows
