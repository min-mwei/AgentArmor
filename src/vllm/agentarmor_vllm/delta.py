"""The chunked Gated DeltaNet update Kai-4B was trained and read with, for serving it unmerged.

Provenance: this routine comes from the Kai project (its training package's delta module) and is
published here by that project's owner so that a served Kai answers with the numerical program
its training scorer ran. It is the chunked gated delta rule in the WY/UT form (as in
transformers' torch_chunk_gated_delta_rule and the DeltaNet papers), written in MLX ops. The
arithmetic below is that module's, operation for operation; only the layout of the source and
the fall-through's keyword arguments differ.

A package that is served unmerged (schema agentarmor-qwen-lora/v1) pins the SHA-256 of this
file: change a byte and such a package is refused before anything is loaded.

How it works:

- within each chunk of C = 64 tokens the delta-rule updates are condensed by the UT transform:
  new values u = T v_beta and decayed keys w = T (k_beta exp(cum)), where T = (I + N)^-1 and N
  is the strictly lower-triangular chunk matrix (k_beta k^T) * decay. T comes from recursive
  2x2 block inversion (log2(C) levels of batched matrix products);
- across chunks the recurrent state is carried by a short sequential scan (T / C steps).

Conventions are mlx-lm's: q and k arrive normalized and scaled by the caller, g is the
per-token, per-head multiplicative decay exp(-exp(A_log) softplus(a + dt_bias)) in (0, 1),
beta = sigmoid(b), and the state is [B, H, Dv, Dk].

Standard library and MLX only.
"""

import mlx.core as mx
import mlx.nn as nn

CHUNK = 64


def _unit_lower_inverse(m):
    """Inverse of a unit lower-triangular m [..., n, n] (n a power of two) by recursive 2x2
    blocking, [[A, 0], [C, D]]^-1 = [[A^-1, 0], [-D^-1 C A^-1, D^-1]], the two half-size
    inverses computed together. Stable: intermediate values are entries of true inverses. (The
    product form prod_j (I + (-N)^(2^j)) is exact in exact arithmetic, but its powers of N
    overflow fp32 on real text, whose neighbouring keys are nearly parallel.)"""
    n = m.shape[-1]
    if n == 1:
        return mx.ones_like(m)
    h = n // 2
    a, c, d = m[..., :h, :h], m[..., h:, :h], m[..., h:, h:]
    inv = _unit_lower_inverse(mx.stack([a, d], axis=-3))
    ai, di = inv[..., 0, :, :], inv[..., 1, :, :]
    ci = -(di @ (c @ ai))
    upper = mx.concatenate([ai, mx.zeros_like(c)], axis=-1)
    return mx.concatenate([upper, mx.concatenate([ci, di], axis=-1)], axis=-2)


def chunked_gated_delta(q, k, v, log_g, beta, state=None, chunk=CHUNK):
    """q, k: [B, T, Hk, Dk]; v: [B, T, Hv, Dv]; log_g, beta: [B, T, Hv]; state: [B, Hv, Dv, Dk]
    or None. Returns y [B, T, Hv, Dv] in q's dtype and the final state [B, Hv, Dv, Dk] in fp32."""
    B, T, Hk, Dk = q.shape
    Hv, Dv = v.shape[-2:]
    out_dtype = q.dtype
    if (r := Hv // Hk) > 1:
        q, k = mx.repeat(q, r, axis=2), mx.repeat(k, r, axis=2)
    # [B, H, T, D] in fp32
    q, k, v = (x.transpose(0, 2, 1, 3).astype(mx.float32) for x in (q, k, v))
    log_g, beta = (x.transpose(0, 2, 1).astype(mx.float32) for x in (log_g, beta))
    pad = (-T) % chunk
    if pad:
        q, k, v = (mx.pad(x, [(0, 0), (0, 0), (0, pad), (0, 0)]) for x in (q, k, v))
        log_g, beta = (mx.pad(x, [(0, 0), (0, 0), (0, pad)]) for x in (log_g, beta))
    n_chunks = (T + pad) // chunk
    v_beta = v * beta[..., None]
    k_beta = k * beta[..., None]

    def shape(x):
        return x.reshape(B, Hv, n_chunks, chunk, x.shape[-1])

    q, k, v_beta, k_beta = (shape(x) for x in (q, k, v_beta, k_beta))
    # log decay from the chunk start to each position
    cum = log_g.reshape(B, Hv, n_chunks, chunk).cumsum(axis=-1)
    lower = mx.tril(mx.ones((chunk, chunk), dtype=mx.bool_))  # j <= i
    # log decay between positions j <= i
    pair = mx.where(lower, cum[..., :, None] - cum[..., None, :], -mx.inf)
    decay = mx.exp(pair)
    strict = mx.tril(mx.ones((chunk, chunk), dtype=mx.bool_), k=-1)
    n = mx.where(strict, (k_beta @ k.swapaxes(-1, -2)) * decay, 0.0)
    t = _unit_lower_inverse(mx.eye(chunk, dtype=n.dtype) + n)
    u = t @ v_beta  # new values
    w = t @ (k_beta * mx.exp(cum)[..., None])  # decayed keys that read the old state
    attn = (q @ k.swapaxes(-1, -2)) * decay  # intra-chunk attention, diagonal included
    q_dec = q * mx.exp(cum)[..., None]
    k_dec = k * mx.exp(cum[..., -1:] - cum)[..., None]
    chunk_decay = mx.exp(cum[..., -1])[..., None, None]  # [B, H, N, 1, 1]
    if state is None:
        s = mx.zeros((B, Hv, Dk, Dv), dtype=mx.float32)
    else:
        s = state.astype(mx.float32).swapaxes(-1, -2)
    outs = []
    for i in range(n_chunks):
        v_new = u[:, :, i] - w[:, :, i] @ s
        outs.append(q_dec[:, :, i] @ s + attn[:, :, i] @ v_new)
        s = s * chunk_decay[:, :, i] + k_dec[:, :, i].swapaxes(-1, -2) @ v_new
    y = mx.stack(outs, axis=2).reshape(B, Hv, n_chunks * chunk, Dv)[:, :, :T]
    return y.transpose(0, 2, 1, 3).astype(out_dtype), s.swapaxes(-1, -2)


_checkpointed = {}


def _chunked(with_state):
    """chunked_gated_delta under mx.checkpoint, as the training scorer runs it: the wrapper
    changes what a backward pass keeps, never a forward value."""
    if with_state not in _checkpointed:
        if with_state:

            def run(q, k, v, g, b, s):
                return chunked_gated_delta(q, k, v, g, b, s)

        else:

            def run(q, k, v, g, b):
                return chunked_gated_delta(q, k, v, g, b)

        _checkpointed[with_state] = mx.checkpoint(run)
    return _checkpointed[with_state]


def gated_delta_update(
    q, k, v, a, b, A_log, dt_bias, state=None, mask=None, *, use_kernel=True, **options
):
    """Drop-in for mlx_lm.models.gated_delta.gated_delta_update (mlx-lm 0.32.2: use_kernel is
    keyword-only): the chunked form for a text body in training mode, mlx-lm's own otherwise.

    A text body in training mode calls with use_kernel=False and, for whole causal rows, no
    mask: that is the chunked path. A kernel call (a body in eval mode, as a merged package is
    served), a mask (a left-padded batch) or any option this file does not implement goes to
    mlx-lm unchanged, so a merged package in the same process is answered as it always was."""
    from mlx_lm.models import gated_delta as reference

    if use_kernel or mask is not None or options:
        return reference.gated_delta_update(
            q, k, v, a, b, A_log, dt_bias, state, mask, use_kernel=use_kernel, **options
        )
    beta = mx.sigmoid(b)
    log_g = -mx.exp(A_log.astype(mx.float32)) * nn.softplus((a + dt_bias).astype(mx.float32))
    if state is None:
        return _chunked(False)(q, k, v, log_g, beta)
    return _chunked(True)(q, k, v, log_g, beta, state)
