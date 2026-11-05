"""Kai encode contract adapted from the trainer snapshot; see src/ORIGIN.md."""

import copy
import hashlib
import re

# Reused rarely-used Qwen special tokens as delimiters (state, q, opt, /opt, decide); no embedding
# rows are added.
SPECIAL = ["<|fim_prefix|>", "<|fim_middle|>", "<|box_start|>", "<|box_end|>", "<|fim_suffix|>"]
MAX_STATE, MAX_BRANCH = 384, 1024
OPT_NONE, OPT_DECIDE = (
    -1,
    -2,
)  # enc["opt"] values: instruction/state/pause tokens, and the <decide> token
PAUSE_TEXT = "."  # the pause token: the tokenizer's own single id for a period

_SPECIAL_RE = re.compile(r"<\|([A-Za-z0-9_]+)\|>")


class ContextOverflow(ValueError):
    """The full record exceeds the checkpoint context."""

    def __init__(self, message, state_tokens=None, max_state=None):
        super().__init__(message)
        self.state_tokens, self.max_state = state_tokens, max_state


def user_tokens(tok, text):
    """Escape reserved token spellings before tokenizing caller text."""
    return tok(_SPECIAL_RE.sub(r"<¦\1¦>", text), add_special_tokens=False).input_ids


def pause_token(tok):
    """Require the checkpoint's pause text to encode as exactly one token."""
    ids = tok(PAUSE_TEXT, add_special_tokens=False).input_ids
    if len(ids) != 1:
        raise ValueError("pause text must encode as one token")
    return ids[0]


def tokenizer_sha256(tok):
    """Hash the canonical fast-tokenizer serialization."""
    return hashlib.sha256(tok.backend_tokenizer.to_str().encode("utf-8")).hexdigest()


def encode(
    tok, rec, max_state=MAX_STATE, max_branch=MAX_BRANCH, strict=True, pauses=0, pause_id=None
):
    """Pack the complete state and isolated question branches with readout positions."""
    if not strict:
        raise ValueError("serving never truncates")
    if pauses:
        if pauses < 0:
            raise ValueError(f"pauses={pauses}: a positive count only")
        if pause_id is not None and pause_id != pause_token(tok):
            raise ValueError(
                f"pause id {pause_id} is not this tokenizer's {PAUSE_TEXT!r} ({pause_token(tok)})"
            )
        pause_id = pause_token(tok)
    state_tokens = user_tokens(tok, rec["state"])
    if strict and len(state_tokens) + 1 > max_state:
        raise ContextOverflow(
            f"state exceeds {max_state} tokens: {len(state_tokens) + 1}",
            state_tokens=len(state_tokens) + 1,
            max_state=max_state,
        )
    S = [tok.convert_tokens_to_ids(SPECIAL[0])] + state_tokens
    ids, seg, pos, opt = list(S), [0] * len(S), list(range(len(S))), [OPT_NONE] * len(S)
    q_id, o_id, c_id, d_id = (tok.convert_tokens_to_ids(t) for t in SPECIAL[1:])
    decide_idx, opt_idx, pause_idx = [], [], []
    for k, q in enumerate(rec["questions"], start=1):
        instr = [q_id] + user_tokens(tok, q["instr"])
        spans = [[o_id] + user_tokens(tok, o) + [c_id] for o in q["options"]]
        br = instr + [t for sp in spans for t in sp] + [pause_id] * pauses + [d_id]
        if len(br) > max_branch + pauses - len(S):
            raise ContextOverflow(
                f"branch too long: {len(br)} tokens with a {len(S)}-token state "
                f"(row limit {max_branch + pauses})"
            )
        base = len(ids)
        p0 = len(S)
        br_opt = (
            [OPT_NONE] * len(instr)
            + [j for j, sp in enumerate(spans) for _ in sp]
            + [OPT_NONE] * pauses
            + [OPT_DECIDE]
        )
        br_pos = list(range(p0, p0 + len(br)))
        ends, cursor = [], len(instr)
        for sp in spans:
            cursor += len(sp)
            ends.append(cursor - 1)
        ids += br
        seg += [k] * len(br)
        pos += br_pos
        opt += br_opt
        decide_idx.append(base + len(br) - 1)
        opt_idx.append([base + e for e in ends])
        pause_idx.append(list(range(base + len(br) - 1 - pauses, base + len(br) - 1)))
    out = {
        "ids": ids,
        "seg": seg,
        "pos": pos,
        "opt": opt,
        "option_isolation": False,
        "decide_idx": decide_idx,
        "opt_idx": opt_idx,
        "labels": [q["label"] for q in rec["questions"]],
        "state_tokens": len(state_tokens) + 1,
        "state_truncated": len(state_tokens) + 1 > max_state,
    }
    if pauses:  # absent without pauses, so a zero-pause encoding is the original layout exactly
        out["pause_idx"] = pause_idx
    return out


def rows_of(enc):
    """Split packed encoding into state and branches with branch-relative readout positions."""
    seg = enc["seg"]
    Ls = seg.count(0)
    rows, start = [], Ls
    for k, (d, oi) in enumerate(zip(enc["decide_idx"], enc["opt_idx"], strict=True), start=1):
        end = d + 1  # <decide> is the last token of its branch
        if seg[start] != k or seg[end - 1] != k:
            raise ValueError("branch layout mismatch")
        rows.append(
            {
                "ids": enc["ids"][start:end],
                "pos": enc["pos"][start:end],
                "decide": d - start,
                "opts": [o - start for o in oi],
                **(
                    {"pauses": [p - start for p in enc["pause_idx"][k - 1]]}
                    if "pause_idx" in enc
                    else {}
                ),
            }
        )
        start = end
    return enc["ids"][:Ls], enc["pos"][:Ls], rows


def canonical(request):
    """A deep copy of the request with every Choice question's criteria sorted by exact key."""
    req = copy.deepcopy(request)
    for q in req["questions"].values():
        if q["type"] == "choice":
            q["criteria"] = dict(sorted(q["criteria"].items()))
    return req
