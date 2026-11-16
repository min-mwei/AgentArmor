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
| `--backend NAME` | `vllm` (default) or `mlx`, the local Apple-silicon backbone; see [Local MLX backbone](#local-mlx-backbone) |
| `--session-package DIR` | A second local package, answered under the `/session` path prefix with its own identity; research and shadow only |
| `--base-dir DIR` | For an unmerged package with `--package` (MLX only): the registered base directory it is served beside; default, the local Hugging Face cache |
| `--verify-only` | Verify the package(s), tokenizer and pointer, print their identities and exit without loading a weight or listening |
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
No trained evidence head exists, so a published package returns no evidence spans; the
path for them is described under [Evidence spans](#evidence-spans).

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

### Local MLX backbone

`--backend mlx` serves the same export on Apple silicon. Install `'./src/vllm[serve-mlx]'`
(MLX and MLX-Metal 0.32.4, MLX-LM 0.32.2) and give the same `--package` or
`--kai-revision`. Only the backbone changes: the export checks, request encoder,
admission limits, FP32 pointer head, temperature and HTTP API are the ones above.
MLX-LM reads `model/` from the verified export directory, never from the hub, and runs
its BF16 weights as stored; any other weight dtype, a quantization or a config naming
its own model code is refused. Each question runs as one complete causal row with no prompt
cache. MLX returns the raw hidden states of the text body (the last layer after the
final norm), the states Kai's MLX trainer feeds the pointer head. Health and every
answer report `backend: "mlx"`.

This is not a vLLM qualification, and vLLM results do not qualify it. Before use,
measure numerical agreement with Kai's MLX reference: logits, probabilities and
decisions on identical tokens and isolated rows, with tolerances set before the read.
The CPU tests check this backbone's contract with stand-ins; they load neither MLX
nor weights.

### Unmerged package (MLX only)

A merged BF16 export does not reproduce Kai's training scorer closely enough on borderline
answers: with the merged export, some probabilities differed from the scorer's by more than
0.05. The second package kind keeps the scorer's numerical program instead. The adapter stays
beside the BF16 base, the text body runs in training mode, and the recurrent update is the
chunked Gated DeltaNet routine in `agentarmor_vllm/delta.py`, published from the Kai project.
`--backend mlx` chooses this path when `kai.json` has schema `agentarmor-qwen-lora/v1`; health
and every answer then report `backend: "mlx-lora"` and `weights: "lora-unmerged"`.

```text
DIR/
  kai.json
  adapter_model.safetensors     # the run's LoRA factors, PEFT names
  pointer.safetensors           # as for a merged export
```

`kai.json` has exactly the merged manifest's `schema`, `base`, `base_revision`, `weights`
(`"lora-unmerged"`), `dtype`, `temperature`, the three limits, `pauses`, `pause_id`,
`tokenizer_sha256`, `evidence` (`"unavailable"`) and `files` (the two files above), and three
more fields:

| Field | Meaning |
|---|---|
| `base_files` | SHA-256 of every file of the registered base used: config, tokenizer, index and each weight shard |
| `lora` | `rank`, `alpha` and the list of `targets` (projection names); every adapter tensor must be a paired factor of a listed target at that rank |
| `runtime` | Exact `mlx`, `mlx-metal` and `mlx-lm` versions, `text_body: "train"`, `delta` (`impl: "agentarmor_vllm.delta"` and that file's SHA-256) and `mlx_lm_sources` (SHA-256 of mlx-lm's `qwen3_5.py` and `gated_delta.py`) |

Everything is checked before a tokenizer, a pointer or a weight is read: the package files,
every base file, the installed versions, the installed mlx-lm sources and this module's own
`delta.py`. A package made for another runtime is refused, never answered by the installed
one. The base comes from `--base-dir` or the local Hugging Face cache snapshot of the
registered revision; the network is never used. Use `--verify-only` to run these checks alone.

The monitor's own server can load either package kind itself (`agentarmor-monitor serve
--kai-package DIR --kai-backend mlx [--kai-base-dir DIR]`); the measurements below were
taken with this service running apart on loopback and the monitor reaching it by address.

The CPU tests cover the manifest, the refusals and the wiring with stand-ins. On 2026-11-15
this loader, in the serving environment alone, answered 320 registered comparisons from two
unmerged packages at a 4,096-token state limit against the training scorer's saved answers on
the same version set. All 320 met the rule registered beforehand: every option probability
within 0.05 (largest difference 0.038), and no answer on the other side of 0.5 except one
whose reference was itself within 0.05 of 0.5. Median request time was 0.83 s and peak device
memory 10.2 GB on Apple silicon. This is numerical agreement on one machine, not an accuracy
or deployment qualification, and it says nothing for vLLM. `policy_ready` stays `false`.

### Evidence spans

No trained evidence head exists and none is published. What exists is the path a head's spans
take and one readout to run a head with; both are unqualified, and `policy_ready` stays
`false`.

An unmerged package may carry a head. Its `evidence` field then names the readout instead of
being `"unavailable"`, and `evidence_head.safetensors` (safetensors, never a pickle) is a
third bound file:

```json
"evidence": {"readout": "joint_pairs_memory_v1", "impl_sha256": "SHA256 of agentarmor_vllm/pairs.py",
             "threshold": 0.0, "max_spans": 32}
```

`readout` must be one this build has (`agentarmor_vllm.engine.EVIDENCE`) whose source file has
that hash; a package that names another, or another source, is refused at load, never served
without its head. A head reads at most 4,096 state tokens.

One readout is published, `joint_pairs_memory_v1` (`agentarmor_vllm/pairs.py`): the span-pair
scorer and decoder of the Kai project, in NumPy, published by that project's owner. It is
arithmetic only: no weights, no data and no training code.

| What the package gives | Rule |
|---|---|
| `evidence_head.safetensors` | Exactly twelve float32 tensors: `k`, `q` (`[P, 2560]`, `[P]`), `pair_memory.hidden` (`[W, 3P]`, `[W]`), `pair_memory.start` and `.end` (`[W, W]`, `[W]`), `pair_memory.bias` (`[1, P]`, `[1]`); `W` even; all finite |
| `threshold` | The score a token pair must exceed to be evidence. The package states it; this code chooses none |
| `max_spans` | 1 to 32 disjoint intervals a question may return |

For each question the readout takes the row's raw hidden states: the state's rows (the state
marker first), the question's own branch and its deciding state. It scores every pair of
state tokens, keeps those above the threshold, and returns the disjoint intervals of greatest
total score as character intervals without white space at their ends. A branch longer than
1,024 tokens is an `error` for that question.

Measured on random tensors (no trained head): against Kai's own module in float32 the scores
agree to 0.00003 at 300 rows and 0.0007 at 2,048 to 4,096 rows, where float32 rotary angles
are themselves that coarse, and the decoder returns the same intervals from the same scores.
On Apple GPUs MLX by default runs float32 matrix products at reduced precision
(`MLX_ENABLE_TF32` unset); the same scorer run that way differed from float32 by 0.07 in
that test. A head fitted under that arithmetic and read here in float32 can therefore mark
different spans near the threshold. Agreement of decoded intervals with the training reader,
by a rule set before the read, is still to be measured for any real head.

With a head loaded, `/health` and every reply say `evidence_available: true`, a reply names
what its evidence is of, and each answer carries its own:

```json
{
  "evidence": {"state_sha256": "…", "state_chars": 2699, "head_sha256": "…",
               "readout": "NAME", "qualified": false},
  "answers": {"decision": {"type": "choice", "choice": "deny", "probabilities": {"…": 0.0},
                           "evidence": {"status": "spans", "spans": [[1204, 1260]]}}}
}
```

| `status` | Meaning |
|---|---|
| `spans` | Half-open intervals of Unicode code points in the exact state that was sent, in order, none overlapping, at most 32 |
| `empty` | The head marked nothing for this question |
| `error` | The head failed, returned something that is not such a list, or the state has no trustworthy token places |
| (no `evidence`) | The service has no head: `evidence_available` is `false` |

Evidence is computed from the same forward pass as the answer and never changes it: an
answer whose evidence is an error is the answer it would have been. Token places come from
the tokenizer's own offsets. The tokenizer composes characters (NFC) before it counts, so a
state that is not already composed gets `error` for every question instead of shifted
offsets; reserved token spellings are escaped one character for one and do not move anything.
