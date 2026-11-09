# Source provenance

## Kai vLLM service

Kai snapshot: `25a73b0fd2bb1d8aced73bb7de5937c0e58f5a59`, Apache-2.0,
https://github.com/min-mwei/Kai.
`src/vllm/agentarmor_vllm/api.py` adapts `train/kai/api.py` (renderer, request
models and answer mapping; date preprocessing and billing helpers omitted).
`encode.py` adapts `train/kai/qwen/encode.py` (delimiter escaping, causal branch
layout, option positions and canonical ordering; training/file helpers omitted,
truncation refused). Formatting and docstrings were simplified. Pointer equations
follow `train/kai/qwen/heads.py`. No training data, weights or checkpoints imported.

The HTTP server, request admission, hook CLI, and cache validation are AgentArmor
code. The service loads a hash-bound Kai export from `min-h-wei/Kai-4B`, preserving
the trained pointer head, tokenizer identity, temperature and input layout.
vLLM 0.33.0 is an external dependency; no vLLM source is vendored.
Kai owns checkpoint selection, merging/export, and numerical qualification.
AgentArmor owns serving integration and application-level qualification.

The deterministic `src/hostfacts` Python modules and their shared fixtures remain
hash-pinned references shared with Kai. They are not automatically wired to the
HTTP service. Their comments retain historical native-port context to preserve
byte identity with the trainer's registered copies; that port has been removed.

## Removed implementations

The user requested removal of C++/Metal and MLX serving on 2026-11-08. Their code,
CMake builds, packaging, tests, and runtime guides were deleted. The branches
were recreated without prior commit history. The records below
preserve upstream provenance; they do not describe active dependencies.

The standalone generation adapter and its stock-model file registry were removed
on 2026-11-09. Kai is the only serving model. The repository now uses `main` only.

- **Kai MLX closure:** commit `034fbb7d12da7dabea3b66a258546a295a025cde`, Apache-2.0.
  The former import copied `train/kai/{__init__,api,suite}.py`,
  `train/kai/core/{__init__,data}.py`, and
  `train/kai/qwen/{__init__,env,encode,delta,backbone,lora,heads,forward,checkpoint,router,objective,score}.py`.
  Only `kai/guard.py` was replaced with a serving shim retaining the model-job
  refusal. The trainer recipe was
  `train/recipes/qwen-rc2-20261107/recovery99-run1-v1/dev-read-v1/read-s0-final582.json`,
  SHA-256 `008e4674d2d636407d74dcbc28be2db4aee8bcac0c23584190835d04b35c0f0e`.
  Its vendored manifest, export tools, and parity code are no longer included.
- **Splash kernels:** commit `031a25a6125af2d7acf7096580afe56eb33d65bc`, Apache-2.0,
  https://github.com/incoai/splash. The removed Metal kernels adapted reductions
  from `runtime/metal/kernels/common/rms_inverse.h` and lane/row mapping from
  `runtime/metal/kernels/prefill/gdn.metal`. Adaptations used standalone FP32
  buffers, explicit epsilon/weight offsets, and dynamic head counts, without
  Splash ABI, GGUF quantization, or DFlash. Upstream retains its license and
  original notices; no Splash code or vendored files remain here.
- **EG2 engine:** imported from `Kai/infer` at
  `164ef847ea920a5297167e5c078dff6a7d704297`; removed in the 2026-11-05 Qwen
  refactor. The import included a numerical engine and weight-free C++ tests.
  Kai's working tree and trainer-owned files were not moved or edited.
- **JSON parser:** nlohmann/json 3.12.0, MIT. Its header was removed with the C++
  client. No nlohmann/json source or vendored files remain here.

Additional historical GemmaEmbed, Hunch, and MLX attribution is retained in
[NOTICE](NOTICE). These removed kernels are not shipped by the current package.
