# Qwen runtime refactor, 2026-11-05

## Source review

Kai was reviewed at `25a73b0fd2bb1d8aced73bb7de5937c0e58f5a59`, specifically
`AGENTS.md`, `train/kai/qwen/{README.md,env.py,encode.py,forward.py,heads.py,checkpoint.py,score.py}`
and `train/kai/api.py`. Its active base is Qwen3.5-4B, pinned at
`1001bb4d826a52d1f399e183466143f4da7b741b`. The 270M EmbeddingGemma model is a
retired training line and historical served comparator, not this refactor's base.
The sibling repository and trainer jobs were not modified.

The current Qwen computation uses causal attention and DeltaNet recurrent state.
The state prefix runs once in the MLX scorer, and each question receives its own
copy of both kinds of cache. Complete state-plus-question rows are its reference
form. Choice order is sorted by exact key, Noul is no/yes, and hidden states at
`<decide>` and `</opt>` feed affine FP32 projections and a scaled dot product.
Checkpoint pauses, tokenizer identity and temperature are semantic inputs.
The frozen evidence head has zero logits and cannot support evidence claims.

Splash was reviewed at `031a25a6125af2d7acf7096580afe56eb33d65bc`, particularly
`runtime/metal/kernels/common/rms_inverse.h`, `shared/normalization.metal`,
`common/gdn_primitives.h` and `prefill/gdn.metal`. Its useful components here
are SIMD reductions and the register-resident DeltaNet scan. DFlash, draft
models, sampling, generation cache policy and GGUF conversion are outside this
one-pass decision workload. Its published speed figures do not transfer to Kai.

## Replacement

The EG2 engine, old encoded HTTP server and architecture-specific tests were
removed. Their source and provenance remain in Git. The native denial adapter,
strict JSON parsing, agent templates and installer contracts remain. New paths:

| Path | Responsibility |
| --- | --- |
| `src/vllm` | Diagnostic Qwen baseline; strict text encoding, raw vLLM hidden states, pointer readout |
| `src/native` | CPU numerical references and native hook client |
| `src/native/metal` | Metal kernels and resource-owning dispatch |
| `tests/native` | CPU and optional GPU primitive contracts, CLI/release tests |

vLLM is a separately installed Python dependency, pinned to 0.19.0. The baseline
uses token embedding without normalization; the pointer head is never replaced
with an LM-head classifier. Complete question rows avoid premature recurrent
cache sharing. The baseline requires a trainer-produced merged HF export.

## Qualification still required

1. Produce and review the exact merged BF16 serving export and FP32 pointer file.
2. Run the pinned vLLM Qwen hybrid pooling path on supported hardware. Compare
   exact encodings and selected hidden states, pointer logits and probabilities
   against the registered MLX scorer, including pauses and multiple questions.
3. Integrate the remaining native Qwen operators, tokenizer, weight loader and
   complete inference graph. Establish BF16 rounding and recurrent-state parity
   before optimizing prefix reuse, precision or quantization.
4. Compare latency and peak memory on matched lengths and branch counts. Validate
   complete input coverage, error denials and agent permission abstention.
5. Train and qualify evidence before returning spans; preserve original character
   offsets through escaping/rendering in any future evidence frontend.

CPU contracts are not model qualification. `policy_ready: false` is unconditional
in both surfaces. There is no complete native Qwen server or production policy
protection in this revision.

## Validation recorded for this refactor

- Python: 38 tests passed; Ruff lint and format checks passed.
- Native Release, ASan/UBSan and separate TSan: six CPU/CLI contracts passed
  in each configuration. Release includes the Objective-C++ Metal host build;
  sanitizer configurations use `AGENTARMOR_METAL=OFF`.
- Native CPU lifetime check: zero leaks reported by macOS `leaks`.
- Metal execution was not run because Kai's device lock was occupied. Offline
  shader compilation was unavailable because the Xcode Metal Toolchain component
  is missing. The shader itself and GPU resource lifetimes remain unvalidated.
- No vLLM model was loaded, no model parity or performance measurements were made,
  and no trainer job was interrupted. The user deferred further runtime testing
  and authorized committing and pushing this development refactor.
