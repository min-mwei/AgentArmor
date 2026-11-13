# Working on AgentArmor

The user requested vLLM-only serving on 2026-11-08. Native C++/Metal and MLX
implementations, build tools, tests, and runtime guides have been removed.
Serving and the lightweight Python hook CLI live in `src/vllm`. Optional
Transformers and MLX training extras are allowed; they do not restore MLX serving.

- Work on `dev`: commit and push there. `main` receives a change only when the user
  asks for it. The user set this on 2026-11-10, when the monitor's commit was moved
  from `main` to `dev`; it replaces the 2026-11-09 arrangement in which `main` was
  the only branch. GitHub Actions is disabled; run checks locally.
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
  - Summarizer is a core part of this repository: `src/summarizer` (package `summarizer`),
    with its own [AGENTS.md](src/summarizer/AGENTS.md). The user decided this on 2026-11-13:
    "Let's refactor AgentArmor and merge Summarizer as a core feature in AgentArmor", and
    "Treat the current Summarizer repo as a POC. The real work is in AgentArmor repo."
    It replaces the 2026-11-10 rule that Summarizer was a dependency whose code was not to
    be copied here. Its hook stays an observer. [docs/architecture.md](docs/architecture.md)
    is the design: one store, one code path for each thing, and nothing copied between
    components. Until the monitor is moved onto this tree it still installs the reviewed
    Summarizer commit named in `src/monitor/pyproject.toml`; that move is told to Kai's
    board first, because it changes what identifies Kai's input.
  - Ask Kai only its registered questions, word for word (`agentarmor_monitor/kai.py`); rules,
    context and the call go in the state, in Kai's decision layout.
  - The gate may return Kai's "deny" for a call Summarizer marked. A call it cannot decide is
    denied unless the operator chose `--on-error abstain`. An allowed or unmarked call abstains.
    "Cannot decide" includes an event it cannot read or record, a gate that cannot start, gaps
    in what Summarizer captured, a call too large to show Kai whole, and an allow given on a
    packet that left key evidence out or held events from after the call.
  - Watching escalates everything Summarizer marks (recall first); an enforcing gate asks
    Kai from medium up until Kai's decisions on real calls are measured. Do not adopt a
    cutoff from the benchset.
  - Shadow first. The user decided on 2026-11-12 that every marked call is to be put to Kai
    before it runs, in shadow before anything is enforced at that level: `serve --shadow`
    and `gate --shadow` ask about every marked call, record what would have been answered
    and stop nothing. A gate's record says what it answered (`stage: answered`), never
    what was enforced: whether the agent obeyed is read from what the hooks report next
    (`stage: observed`). Do not count enforcement from a gate's answers alone.
  - The proposed call is redacted like the packet before it is sent. An allow counts only
    when the call was shown whole and the evidence is from before it; a request built from
    less is sent marked incomplete. Kai is asked off the summary loop: no Kai request may
    delay a session's summary.
  - `agentarmor-monitor serve` is the local server. Summarizer and its model run inside it.
    The same server may run Kai-4B, and a decision model may instead run remotely. The user
    set this on 2026-11-11. That the server loads Kai into its own process is how it is
    built, not part of that decision.
  - The server owns what a decision rests on: the rules, its record of the session, the
    request, the way to the decision model and the check that what answered is the model
    configured (`kai.profile`). A gate names a call and is told the outcome
    (`POST /v1/decide`). Do not give a gate rules, a decision model's address or a
    credential, and do not let anything a caller sends become policy, evidence or
    provenance. The model's own interface is on the port only with `--model-api`.
  - Session text leaves the machine only from the local server, only when the operator
    passes `--allow-remote`, only over `https`, and only to the one configured address: no
    redirect is followed and no proxy is used for an address on this machine. Its token is
    read from a file, or from the environment for a service elsewhere, and is never printed,
    logged or reported. Do not log packet text.
  - Kai on Summarizer packets is unmeasured and serving is unqualified: keep
    `policy_ready: false` in every record and do not describe the monitor as attack prevention.
  - A gate denies on the errors it can see. Whether a call is stopped when the gate cannot
    run at all is the client's: the Claude Code `PreToolUse` handler carries
    `"onFailure": "block"` (2.1.295 or later) and no other handler does; Codex has no such
    setting, and does not put `write_stdin` to a hook. Do not describe a Codex gate as
    failing closed, and do not run a gate beside a hook that rewrites tool input.
  - The server loads an encoder with the probes calibrated for it (`--probes`, or the model
    directory's own `probes.jsonl`) and refuses a model directory without them unless
    `--uncalibrated` is given. `/health` says which probes are loaded and whether they are
    calibrated.
- Run `ruff check .`, `ruff format --check .`, and `pytest` with the project
  environment. Distinguish CPU adapter contracts from GPU/model validation.
  CMake, CTest, and native sanitizer builds are no longer part of this repository.
- The serving integration is unqualified. Keep `policy_ready: false` and explicit
  synchronous hook denials until trainer-owned qualification passes. Do not
  describe this build as production attack prevention.
- Keep the published benchset and hash-pinned host-fact reference modules intact.
  Their historical implementation comments do not change the vLLM-only direction.
