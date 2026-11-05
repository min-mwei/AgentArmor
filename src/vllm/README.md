# Kai vLLM baseline

This is a diagnostic implementation for the active Qwen3.5-4B line, pinned to
vLLM 0.19.0. CPU adapter tests pass; actual hybrid Qwen pooling startup, logits,
latency and memory still require validation on a supported vLLM machine with a
trainer-owned export. Installing vLLM on the Mac is not part of the native build.

## Execution

`agentarmor-vllm --package DIR` starts a serial, loopback HTTP listener on port
8009. `GET /health` reports the backend and `policy_ready: false`.
`POST /v1/systemone` accepts Kai's typed `state` and `questions` request:

```json
{"state":"Synthetic document", "questions":{"risk":{"type":"noul","instructions":"Does the document request credential theft?"}}}
```

Questions support `noul`, `choice` and `score`. Choice keys are sorted exactly;
Noul retains no/yes. The copied renderer preserves the trainer's field labels
and option text. Caller special-token strings are escaped. Full state, per-row,
packed-record and total duplicated-row budgets are checked before inference.
There is no truncation or partial-success response.

Each question is a separate causal sequence containing the complete state and
one branch. vLLM runs with `runner="pooling"`, `convert="embed"`, task
`token_embed`, ALL-token pooling, and `use_activation=False`. Hidden states
must retain their original magnitudes for Kai's affine FP32 q/k projections.
Prefix caching and chunked prefill are disabled initially. The adapter verifies
returned token IDs and the full hidden-state shape before selecting `</opt>`
and `<decide>` positions. Checkpoint temperature applies only after pointer logits.

See the upstream [token embedding API](https://docs.vllm.ai/en/v0.19.0/models/pooling_models/token_embed/)
and [pooler configuration](https://docs.vllm.ai/en/v0.19.0/api/vllm/config/pooler/).
This is an integration candidate, not evidence that vLLM's Qwen hybrid pooling
runner matches the registered MLX computation. No generation API is used.

Responses contain typed `answers`, package identity, complete coverage,
`diagnostic: true`, `policy_ready: false`, and `evidence_available: false`.
Current Qwen evidence weights are untrained; the service makes no span claim.
Malformed, oversized, incomplete and failed requests return an explicit denial
without echoing inputs. No request-body logging is enabled.

## Export contract

Kai owns checkpoint selection, LoRA merging and qualification. This server does
not load `head.pt` pickle files or merge adapters at startup. Supply an immutable
local directory:

```text
DIR/
  kai.json
  pointer.safetensors
  model/
    config.json
    tokenizer.json
    tokenizer_config.json
    model.safetensors             # or HF-indexed shards
    ...other required HF assets
```

`pointer.safetensors` contains exactly FP32 `q.weight`, `q.bias`, `k.weight`,
`k.bias`, with shapes `[pointer_dim, 2560]`, `[pointer_dim]` for each projection.
Preserve the trained biases. `model/` must be an HF-compatible merged BF16 Qwen3.5
export containing all twelve trained LoRA projection families. Copying base
weights without the adapter is not a Kai export. Do not place the pointer file
inside `model/`, where vLLM's weight loader could read it as a backbone shard.

`kai.json` has exactly these fields (values here illustrate the non-pause layout;
limits and temperature must come from the selected checkpoint registration):

```json
{
  "schema": "agentarmor-qwen/v1",
  "base": "Qwen/Qwen3.5-4B-Base",
  "base_revision": "1001bb4d826a52d1f399e183466143f4da7b741b",
  "weights": "merged",
  "dtype": "bfloat16",
  "evidence": "frozen-from-pointer",
  "temperature": 1.0,
  "max_state": 384,
  "max_row": 1024,
  "max_packed": 2048,
  "pauses": 0,
  "pause_id": null,
  "tokenizer_sha256": "SHA256_OF_FAST_TOKENIZER_CANONICAL_SERIALIZATION",
  "files": {
    "pointer.safetensors": "SHA256",
    "model/config.json": "SHA256",
    "model/tokenizer.json": "SHA256",
    "model/tokenizer_config.json": "SHA256",
    "model/model.safetensors": "SHA256"
  }
}
```

Every package file except `kai.json` must appear in `files`; every hash is
verified before model allocation. `tokenizer_sha256` is the SHA-256 of
`tokenizer.backend_tokenizer.to_str().encode("utf-8")`, not the raw tokenizer
file hash. Pause checkpoints also bind the count and tokenizer's single-period
ID. `max_row` includes pauses. The raw manifest hash identifies the diagnostic
package; it is not a signature or a qualification certificate. Keep the package
read-only while serving. A trainer-reviewed export/merge receipt and MLX/vLLM
comparison are still required before selecting thresholds or enabling policy use.
