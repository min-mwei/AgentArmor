# AgentArmor

AgentArmor is a native policy gate for Codex and Claude Code. Users author the
policies; Kai, the official approximately 270M EmbeddingGemma-2 decision encoder,
provides semantic decisions through its trained pointer and evidence heads.
The architecture is `Codex / Claude <--> agentarmor executable`, with hook,
CLI, and resident server modes sharing one evaluation implementation.

**Development status:** the CPU/Apple Metal engine, encoded diagnostic server,
strict input parsing, and weight-free security contracts are implemented.
Native text tokenization, policy evaluation, package qualification, and complete
hook integration remain in progress. `agentarmor doctor` reports
`policy_ready: false`. Synchronous hooks currently deny every check;
observation hooks report incomplete coverage without scanning. This build is
not ready for policy-based production enforcement.

The Python service, `armor/` package, agent launchers, installer, and project
metadata have been removed. Python is used only for optional developer tests.
Kai's canonical MLX training/export/parity tools remain in the Kai repository.

## Install

The intended release installation command is:

```sh
curl -fsSL https://github.com/min-mwei/AgentArmor/releases/latest/download/install.sh | bash
```

The installer downloads a prebuilt arm64 executable, verifies SHA-256, and
installs it in `~/.local/bin` (override with `AGENTARMOR_INSTALL_DIR`). It needs
no Python, compiler, sudo, or separate Kai process. The initial platform is
macOS 15+ on Apple Silicon M4+, with M5 Max Metal optimization as the main
performance target. Initial releases may be unsigned; notarization is excluded.
This change does not publish a release, so the command needs prepared GitHub
release assets before it can be used.

## Build and check

The source layout is `src/kai` for inference, `src/server` for the executable
and transport, `tests/native` for C++ contracts, and `third_party` for pinned
dependencies. Executables are built under `build/<config>/bin`.

Use CMake and a native Apple Silicon Xcode toolchain:

```sh
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=Release
cmake --build build/release -j 4
ctest --test-dir build/release --output-on-failure -LE metal-compile
build/release/bin/agentarmor doctor
cmake --install build/release --prefix "$HOME/.local"
```

`--device cpu|gpu|auto` selects the diagnostic server's startup mode:

```sh
agentarmor serve --encoded-only --device cpu --weights /path/to/merged-export
agentarmor serve --encoded-only --device gpu --weights /path/to/merged-export
```

GPU means Apple Metal; explicit GPU mode never falls back silently. `auto`
chooses Metal when available, otherwise CPU, at startup. CPU-only builds use
`-DAGENTARMOR_METAL=OFF`. Encoded diagnostics accept token/layout arrays and
return logits; they are not the text policy API. Model-backed runs require a
trainer-approved device window and the exact qualified package.

Optional development checks use Python 3.11+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
```

These checks exercise shipped templates against the built native executable,
including paths containing spaces and execution without Python on `PATH`.
Override the test binary with `AGENTARMOR_TEST_BINARY`. Native security checks
also require separate ASan/UBSan, TSan, and leak runs; see the
[security guide](docs/native_security.md). Weight-free contracts do not qualify
model accuracy, GPU safety, evidence, or latency.

## Agent hooks and policies

[Codex templates](agents/codex_code/README.md) and
[Claude Code templates](agents/claude_code/README.md) invoke an absolute
`agentarmor` path directly. They currently block synchronous tool use until the
native policy frontend is qualified. Installing these development templates
does not establish attack prevention.

[Example policy](agents/policy.example.jsonl) uses one concise rule per JSONL
line: `id`, `tier`, and `description`. The
[smoke policy](agents/policy.smoke.jsonl) protects one marker file. AgentArmor
will parse these files and render valid rules in Kai's registered training
format; the native policy loader is not yet implemented.
Qualified allows must abstain from the agent's existing permission flow;
synchronous errors must deny. Background observations are advisory and cannot
undo completed actions. Whole inputs must be scanned or explicitly refused.

## Design and development

- [Attack research: sources, dates, tactics, and examples](docs/agent_attacks.md)
- [AgentArmor architecture and attack-by-attack refactoring](docs/AgentArmor.md)
- [Kai trainer requirements and native qualification](https://github.com/min-mwei/Kai/blob/dev/docs/Kai_requirements.md)
- [Native security review, local tests, and release gates](docs/native_security.md)
- [Historical model results](docs/benchmarks.md)

Work on `dev`; `main` is reserved for reviewed releases. GitHub Actions is
disabled; run checks locally. Do not commit weights, secrets, or raw chat logs.
