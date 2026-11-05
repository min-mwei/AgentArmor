# Runtime source provenance

## Qwen refactor, 2026-11-05

Kai snapshot: `25a73b0fd2bb1d8aced73bb7de5937c0e58f5a59`, Apache-2.0.
`src/vllm/agentarmor_vllm/api.py` adapts `train/kai/api.py` (renderer, request
models and answer mapping; date preprocessing and billing helpers omitted).
`encode.py` adapts `train/kai/qwen/encode.py` (delimiter escaping, causal branch
layout, option positions and canonical ordering; training/file helpers omitted,
truncation refused). Formatting and docstrings were simplified. Pointer equations
follow `train/kai/qwen/heads.py`. No training data, weights or checkpoints imported.
The native strict JSON parser and hook adapter are retained from AgentArmor's
previous implementation; the EG2 numerical engine and server were removed.

Splash snapshot: `031a25a6125af2d7acf7096580afe56eb33d65bc`, Apache-2.0,
https://github.com/incoai/splash. `src/native/metal/kernels.metal` adapts the
reduction in `runtime/metal/kernels/common/rms_inverse.h` and lane/row mapping
in `runtime/metal/kernels/prefill/gdn.metal`. Adaptations use standalone FP32
buffers, explicit epsilon/weight offset, dynamic head counts, no Splash ABI,
no BF16/GGUF quantization and no DFlash. Normalization and gate helpers were
reviewed but not imported wholesale. Splash license is retained in
`third_party/splash.LICENSE`; its original notices accompany it.

vLLM is a pinned external dependency (0.19.0); no vLLM code is vendored.
Kai owns checkpoint selection, merge/export and qualification; AgentArmor owns
the serving adapter and experimental native implementation.

## Historical import (engine removed by the Qwen refactor)

Imported production engine and weight-free C++ tests from `Kai/infer` at
commit `164ef847ea920a5297167e5c078dff6a7d704297`.
The Kai working tree and trainer-owned files were not moved or edited.

AgentArmor organizes the imported engine in `src/kai`, runtime/transport in
`src/server`, weight-free tests in `tests/native`, and vendored headers/licenses
in `third_party`.

Local changes begin with the macOS build, native command surface, model parsing,
and security regression tests. Preserve this provenance and the accompanying
NOTICE and third-party license files when updating the snapshot.

Kai continues to own training, checkpoint selection, merged exports, and MLX
reference/parity tooling. AgentArmor owns the production executable and its
CPU/Metal runtime. Synchronize future engine changes through explicit reviewed
updates; do not let two independent production copies evolve.
