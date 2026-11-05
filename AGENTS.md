# Working on AgentArmor

Kai now uses Qwen3.5-4B-Base. The user requested the Qwen/vLLM and native Metal
refactor on 2026-11-05; the previous EG2-only and no-Python-serving rules are retired.
Python serving is isolated in `src/vllm`; the native hook client has no Python dependency.

- Work on `dev`. `main` is reserved for reviewed releases. Keep these as the two
  permanent branches.
- GitHub Actions is disabled for this repository. Run checks locally.
- Keep agent-specific templates and guides in `agents/codex_code` and
  `agents/claude_code`; share native adapter and evaluation behavior in the
  executable. Templates must invoke an absolute `agentarmor` path directly.
- Keep the vLLM baseline in `src/vllm`, native code in `src/native`, Apple kernels
  in `src/native/metal`, C++ contracts in `tests/native`, and vendored dependencies
  in `third_party`.
  Preserve attribution and the import provenance in `src/ORIGIN.md`.
- Kai is a decision model. Preserve its pointer readout, delimiter escaping,
  isolated question rows, checkpoint temperature, and evidence offsets. Do not
  substitute generated text or vocabulary-logit classification for its head.
- CPU builds and hook clients must work without a GPU runtime or Python. Do not
  commit model weights, chat logs, secrets, or `.env`.
- Synchronous checks must emit an explicit denial on errors. An allowed check
  abstains from the agent's existing permission flow. Background scans are
  advisory and must not claim to prevent a completed tool's side effects.
- Preserve whole-input scan coverage. Refuse oversized or unencodable input;
  never silently truncate a document or return a clear verdict after a partial scan.
- Run `ruff check .`, `ruff format --check .`, and `pytest` with the project
  environment for the baseline and dev/test scripts. Build and run native CTest contracts
  in Release, ASan/UBSan, and separate TSan configurations; check leaks after
  native lifetime changes. Distinguish CPU contracts from GPU/model validation.
- Native text policy/tokenizer integration is unqualified. Keep `policy_ready:
  false` and explicit synchronous denials until trainer-owned qualification
  passes. Do not describe this build as production attack prevention.
