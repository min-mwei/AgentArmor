# Working on AgentArmor

The user requested vLLM-only serving on 2026-11-08. Native C++/Metal and MLX
implementations, build tools, tests, and runtime guides have been removed.
Serving and the lightweight Python hook CLI live in `src/vllm`. Optional
Transformers and MLX training extras are allowed; they do not restore MLX serving.

- Work on `main`, the only permanent branch. The user requested removal of `dev`
  and a single-commit main history on 2026-11-09. Do not recreate `dev`.
  GitHub Actions is disabled; run checks locally.
- Keep agent-specific templates and guides in `agents/codex_code` and
  `agents/claude_code`. Templates invoke an absolute `agentarmor` path directly.
  The CLI requires Python, but must not import vLLM, Torch, or a GPU runtime.
- Kai-4B is the only serving model. Requests cannot switch models or trigger
  downloads. Preserve attribution and source provenance in `src/ORIGIN.md` and
  `src/NOTICE`.
- Kai is a decision model based on Qwen3.5-4B-Base. Preserve its pointer readout,
  delimiter escaping, isolated question rows, checkpoint temperature, and
  evidence offsets. Do not replace its head with generation or vocabulary logits.
  Kai owns checkpoint selection, merged exports, and numerical qualification.
- Kai loads a trainer-published `vllm/` export from `min-h-wei/Kai-4B` at an explicit
  HF commit. Verify every artifact before allocating the model and refuse absent
  or incompatible exports. Load only the text backbone and trained pointer head.
- Do not commit model weights, chat logs, secrets, or `.env`.
- Synchronous checks must emit an explicit denial on errors. An allowed check
  abstains from the agent's existing permission flow. Background scans are
  advisory and cannot prevent a completed tool's side effects.
- Preserve whole-input coverage. Refuse oversized or unencodable input; never
  silently truncate a document or return clear after a partial scan. Admit all
  question rows before inference starts.
- Run `ruff check .`, `ruff format --check .`, and `pytest` with the project
  environment. Distinguish CPU adapter contracts from GPU/model validation.
  CMake, CTest, and native sanitizer builds are no longer part of this repository.
- The serving integration is unqualified. Keep `policy_ready: false` and explicit
  synchronous hook denials until trainer-owned qualification passes. Do not
  describe this build as production attack prevention.
- Keep the published benchset and hash-pinned host-fact reference modules intact.
  Their historical implementation comments do not change the vLLM-only direction.
