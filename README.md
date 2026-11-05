# AgentArmor

AgentArmor is being rebuilt around Kai's Qwen3.5-4B decision model. The old
EmbeddingGemma engine and encoded server have been removed from `src` (their
history remains in Git).

- `src/vllm`: Python diagnostic baseline, using vLLM's causal token hidden
  states and Kai's trained pointer head. It serves `POST /v1/systemone`.
- `src/native`: C++ numerical references and the native `agentarmor` hook client.
- `src/native/metal`: Apple Silicon kernels and Objective-C++ dispatch for RMSNorm,
  projected pointer scores, and the 128-dimensional DeltaNet recurrence.

**Status:** this is an unqualified development implementation. The vLLM adapter
has weight-free tests but has not been run against a Qwen export on supported
hardware. Native Qwen weight loading, transformer execution, tokenization and
serving are not implemented. Metal kernels are not yet performance-qualified.
`policy_ready` remains false, hooks deny synchronous checks, and background
observations report incomplete coverage. No production attack-prevention claim
is made. Qwen's current evidence head is frozen and untrained; spans are not returned.

## vLLM baseline

Use a separate Linux environment supported by vLLM; the native Mac client does
not depend on it. See [baseline setup and package contract](src/vllm/README.md).

```sh
python -m pip install ./src/vllm
agentarmor-vllm --package /path/to/qualified-merged-export --port 8009
```

The listener binds to loopback. It requires the trained, merged model and its
pointer tensors, not the base model alone. Each question runs as a complete
independent causal row, preserving both attention and recurrent-state isolation.
There is no generation, draft model, DFlash, or vocabulary-logit classification.

## Native build

```sh
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=Release
cmake --build build/release -j 4
ctest --test-dir build/release --output-on-failure -LE gpu
build/release/bin/agentarmor doctor
```

Use `-DAGENTARMOR_METAL=OFF` for CPU-only contracts and clients. The native target
is macOS 15+ arm64. `agentarmor serve` was removed with the EG2 backend; it now
fails explicitly. The client remains independent of Python and GPU runtimes.

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
```

Run sanitizer, leak and optional Metal checks as described in
[local validation](docs/native_security.md). Tests use synthetic data, not weights.

## Integration and provenance

[Codex](agents/codex_code/README.md) and [Claude Code](agents/claude_code/README.md)
templates invoke an absolute native executable path. They remain fail-closed
until an end-to-end policy evaluator is qualified. Diagnostic probabilities
never grant an agent permission.

The [source review](docs/runtime_refactor.md) records the Kai and Splash findings,
what was removed, and the remaining integration work.
[Source provenance](src/ORIGIN.md) and [notices](src/NOTICE) accompany copied code.
The older [architecture proposal](docs/AgentArmor.md) is historical.

Work on `dev`; `main` is reserved for reviewed releases. GitHub Actions is disabled.
Do not commit model weights, secrets or chat logs. Local packaging scripts remain
available, but currently package only the unqualified native client.
