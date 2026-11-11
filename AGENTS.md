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
- The local monitor lives in `src/monitor` (`agentarmor-monitor`). The user set its shape on
  2026-11-10: Summarizer runs as a local model that reads every agent interaction, summarizes
  the session as context and filters out suspicious activity; Kai receives each suspicious
  entry with that context and makes the final decision, allow or deny.
  - Summarizer (`github.com/min-mwei/Summarizer`) is a dependency. Its hook stays an observer;
    do not copy its code here or change its summary format from this repository.
  - Ask Kai only its registered questions, word for word (`agentarmor_monitor/kai.py`); rules,
    context and the call go in the state, in Kai's decision layout.
  - The gate may return Kai's "deny" for a call Summarizer marked. A call it cannot decide is
    denied unless the operator chose `--on-error abstain`. An allowed or unmarked call abstains.
    "Cannot decide" includes an event it cannot read or record, a gate that cannot start, gaps
    in what Summarizer captured, a call too large to show Kai whole, and an allow given on a
    packet that left key evidence out or held events from after the call.
  - Watching escalates everything Summarizer marks (recall first); the gate asks Kai from
    medium up until Kai's decisions on real calls are measured. Do not adopt a cutoff from the
    benchset.
  - The proposed call is redacted like the packet before it is sent, and shown whole or not
    at all. Kai is asked off the summary loop: no Kai request may delay a session's summary.
  - Session packets go only to a loopback Kai service unless the operator passes
    `--allow-remote`. Do not log packet text.
  - Kai on Summarizer packets is unmeasured and serving is unqualified: keep
    `policy_ready: false` in every record and do not describe the monitor as attack prevention.
- Run `ruff check .`, `ruff format --check .`, and `pytest` with the project
  environment. Distinguish CPU adapter contracts from GPU/model validation.
  CMake, CTest, and native sanitizer builds are no longer part of this repository.
- The serving integration is unqualified. Keep `policy_ready: false` and explicit
  synchronous hook denials until trainer-owned qualification passes. Do not
  describe this build as production attack prevention.
- Keep the published benchset and hash-pinned host-fact reference modules intact.
  Their historical implementation comments do not change the vLLM-only direction.
