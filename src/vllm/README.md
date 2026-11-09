# Kai vLLM service

The service loads one Kai-4B export at startup and accepts complete requests on
`127.0.0.1`. It returns typed probabilities from Kai's trained pointer head.
`policy_ready: false` and `evidence_available: false` remain explicit: model
scores are available for evaluation, and the agent hooks are not connected to them.

## Install and start

Use Python 3.11–3.13 on a supported Linux GPU host. The serving extra pins
[vLLM 0.33.0](https://docs.vllm.ai/en/v0.33.0/), Transformers 5.17.0 and HF Hub 1.32.0.
CPU development and the hook CLI can install the base package without vLLM.

```sh
python3 -m venv .venv
.venv/bin/pip install './src/vllm[serve]'
.venv/bin/agentarmor-vllm \
  --kai-revision 5bd63725365b947d658a53d99bc14d751fa9f7e1 \
  --port 8009
```

The revision above contains the published `vllm/` export in
[min-h-wei/Kai-4B](https://huggingface.co/min-h-wei/Kai-4B/tree/5bd63725365b947d658a53d99bc14d751fa9f7e1/vllm).
Startup downloads only that subdirectory, verifies its manifest and every artifact,
and checks the tokenizer and pointer head before allocating the GPU engine.
The export's trained adapter is already merged into the BF16 backbone. A base
model or an unmerged training checkpoint cannot replace this export.

| Option | Meaning |
|---|---|
| `--kai-revision SHA` | Full 40-character commit in the Kai-4B HF repository |
| `--package DIR` | Local directory containing `kai.json`, `pointer.safetensors` and `model/`; mutually exclusive with `--kai-revision` |
| `--local-files-only` | Require the HF export to be cached; no download |
| `--cache-dir DIR` | Override the Hugging Face hub cache location |
| `--port PORT` | Loopback HTTP port; default 8009 |

HF cache options apply only to `--kai-revision`. Otherwise Hugging Face uses
`HF_HOME` / `HF_HUB_CACHE` or its standard cache directory. `--model kai-4b` is
accepted for existing launch commands; Kai is the only supported model.
Context limits come from the export and cannot be enlarged by a launch option.
Keep the package/cache immutable while serving. No request triggers a model download.

## Request and response contract

`GET /health` returns the model, pointer readout, package hash and readiness.
For an HF-loaded export it also returns the repository and exact revision.
`POST /v1/systemone` accepts `state` and 1–64 typed `questions`:

```json
{
  "state": "Synthetic document",
  "questions": {
    "risk": {
      "type": "noul",
      "instructions": "Does the document request credential theft?"
    }
  }
}
```

`state` can be a string or structured JSON. Include the policy, user task, relevant
history and proposed operation when asking about authorization. AgentArmor does
not collect or authenticate that context for the caller.

The optional `model` accepts `kai`, `kai-4b` or `Kai-4B`. Omit it to use the
resident model; other values fail admission.

| Question type | Criteria | Answer |
|---|---|---|
| `noul` (yes/no) | Optional `false` / `true` descriptions | `{"type":"noul","noul":0.8}` means probability of true |
| `choice` | Object with 1–255 labeled options | Selected `choice`, per-option `probabilities` and maximum probability as `confidence` |
| `score` | Array with 1–255 ordered levels | Expected `score`, `probabilities`, `confidence` and `legend` |

Every response identifies `readout: "pointer"`. A successful response returns
scores, not an authorization decision. `coverage: "complete"` means the entire
supplied state and all questions were processed; it does not establish that the
request included sufficient facts or that the model reached a correct conclusion.

Requests are limited to 1 MB of strict UTF-8 JSON. Duplicate keys, invalid Unicode,
nonfinite values, unknown fields, mismatched models and excess nesting are rejected.
All question rows must fit before any inference starts. Each row receives the full
state, and aggregate work is limited to 131,072 row tokens. The published export
allows 2,048 state tokens, 2,694 tokens per row (including six pause tokens), and
3,712 tokens in the packed request layout. Delimiters and question text count toward
these limits. Longer input is rejected; no truncation or partial success is allowed.

Malformed input returns HTTP 400, oversized bodies return 413, and inference failures
return 503. Errors include `decision: "deny"`, `policy_ready: false` and incomplete
coverage without echoing the request. Request-body logging is disabled. The server
is serial and loopback-only; it does not authenticate other local processes.

## Kai execution

The service preserves the trainer's renderer, canonical option order, no/yes
positions, delimiter escaping, decision offsets, pause tokens and temperature.
It loads only the text backbone from the published multimodal HF configuration.
vLLM supplies raw causal hidden states using `runner="pooling"`, `convert="embed"`,
task `token_embed`, ALL-token pooling and `use_activation=False`.

Before applying Kai's FP32 affine q/k pointer head, the service checks the returned
token IDs and hidden-state shape. Each question runs as an independent causal row,
including its full state prefix. This isolates both attention and DeltaNet recurrent
state. Prefix caching and chunked prefill are disabled. The head's logits are
scaled by the checkpoint temperature and converted to probabilities.

See the upstream [token embedding API](https://docs.vllm.ai/en/v0.33.0/models/pooling_models/token_embed/)
and [pooler configuration](https://docs.vllm.ai/en/v0.33.0/api/vllm/config/pooler/).
CPU tests check the encoding, export, pointer readout and HTTP behavior. Actual
hybrid pooling startup, numerical agreement and latency need a supported GPU host.
The frozen evidence head is not qualified, so the service returns no evidence spans.

## Qualification

CPU verification on 2026-11-09 checked the pinned export above: all 11 artifact
hashes, the tokenizer identity, six pause tokens, the 256-dimensional pointer
head and the model's shard index passed. All 1,654 published benchset requests
fit the complete-input limits. The 280 CPU tests also passed. These checks did
not execute the backbone or measure GPU results.

Keep `policy_ready: false` until the exported model passes numerical comparison
with Kai's training scorer and deployment evaluation on a supported vLLM GPU host.
Record complete-input behavior, repeatability, attack recall, false-positive rates,
and memory/latency under load. Training-runtime benchmark results do not qualify
the serving implementation. Hook coverage, trusted context assembly, transport,
and denial/abstention behavior also need end-to-end validation before enforcement.

## Kai export contract

Kai owns checkpoint selection, LoRA merging and qualification. This server does
not load `head.pt` pickle files or merge adapters at startup. Supply an immutable
local directory, or publish the same tree under `vllm/` in the Kai HF repository:

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
read-only while serving. A trainer-reviewed export/merge receipt and comparison
against the trainer’s registered read computation are still required before
selecting thresholds or enabling policy use.
