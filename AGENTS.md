# Working on AgentArmor

On 2026-11-14 the user chose **"Use vLLM Metal or MLX"** for local Apple Silicon
serving. This supersedes the 2026-11-08 vLLM-only restriction. The current serving
implementation and lightweight Python hook CLI live in `src/vllm`; a local backend
must use the same verified Kai artifacts, request encoder, pointer head and API.
The local backend is not yet implemented or qualified. Do not restore the removed
native C++ stack or substitute a generative/vocabulary readout to obtain Metal support.
The bounded implementation and qualification path is in
[docs/stack-qualification-plan.md](docs/stack-qualification-plan.md#7-agentarmor-measure-the-path-users-actually-run).

- Work on `dev`: commit and push there. `main` receives a change only when the user
  asks for it. The user set this on 2026-11-10, when the monitor's commit was moved
  from `main` to `dev`; it replaces the 2026-11-09 arrangement in which `main` was
  the only branch. GitHub Actions is disabled; run checks locally.
- Keep agent-specific templates and guides in `agents/codex_code` and
  `agents/claude_code`. The installable examples there invoke an absolute
  `agentarmor-monitor` path directly (the monitor's gate, in shadow) and are what
  `agentarmor-monitor hooks-config` prints. The older `agentarmor hook` command is the
  deny-all fixture in `agents/*/fixture`, for tests only. A hook process stays light: it
  requires Python and must not import vLLM, Torch, a GPU runtime or any model.
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
  A local MLX/Metal backend must establish compatibility with that export or use a
  versioned, hash-pinned compatible export published by Kai. Its backend, numerical
  profile and context limit require their own qualification; a training-runtime
  result is not a serving result.
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
  - Summarizer is a library of this repository: `src/summarizer` (package `summarizer`).
    The tools that measure and train its encoder are in `training/summarizer`. It comes
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
  Their historical implementation comments do not change the current serving direction.

## Summarizer: its rules, moved here from the retired Summarizer repository

Summarizer is a library of AgentArmor: the in-tree package in `src/summarizer/` and its training
and measurement in `training/summarizer/`. Its repository is being retired at the user's request
(2026-11-13: "Migrate/Move all training data etc. into AgentArmor"); these rules, which the user set
there, apply here.

- **Roles.** Three agents work on it: Model Trainer, Model Reviewer and Data Engineer.
  [agent_swarm.md](agent_swarm.md) says what each owns and how to start it. They hand work over
  through the shared board, `../msgboard.md` beside this repository, which is append-only: never
  edit another role's entry.
- **Data engineering runs on Codex Daybreak Blue.** The Data Engineer, who writes and repairs
  synthetic data, is Codex CLI `gpt-daybreak-blue-latest` at medium reasoning effort (high for a
  hard batch, said on the board). A reader that judges data blind, as a qualification does, is
  Codex `gpt-daybreak-blue-latest` at xhigh effort, run with no tools and no files. Claude Opus
  and Sonnet do not author or blind-read data: their safety filters refuse synthetic credential and
  exfiltration content, and the work does not try to get around them. A blind reader on the
  author's own model is less independent, and its results say so. The user set this on
  2026-11-13.
- **The summarizer tags; it does not judge.** It marks entries and observables with indicators
  (label, confidence, basis) and a stronger model decides what is an attack. An entry can be
  suspicious without being one. Recall matters more than precision: do not add a gate that
  suppresses an indicator because it is common in benign sessions; give it a low confidence instead.
- **It watches what the agent does, not the quality of what it writes.** The concern is an agent
  compromising security by its actions: sending data out, suspicious file activity, reaching
  credentials, changing controls. Insecure code in the agent's own output is the coding agent's
  job, and tamper-proofing the event log is not planned. Detectors are tuned for recall; Kai is the
  low-false-positive stage. The user set this on 2026-11-10.
- **Where it runs.** For AgentArmor end to end, Summarizer and its model run inside an AgentArmor
  local server. That server may also run Kai-4B, and a decision model such as Kai-4B may instead
  run remotely in a cloud environment. The user set this on 2026-11-11.
- **It reads everything the agent reads and runs**: prompts, tool calls, tool results, instruction
  files. Anything dropped (clipped, unscanned, unreadable) must show up in `coverage`; never report
  a partial view as complete.
- **A summary is evidence for a decision about one tool call.** An attempt is never recorded as an
  outcome: an action's status says what a hook reported, and `returned` means a result came back,
  not that an effect happened. A later mention never rewrites who had named something at the time.
  Confidence is a ranking score, not a probability.
- **Artifacts are JSON Lines.** `events.jsonl`, `summary.jsonl`, probe sets, training data and
  session cases hold one record per line.
- **The summary is extractive.** The embedding model ranks, links and tags; it never writes text.
  Provenance (user, agent, lower-trust) comes from which hook delivered the text, never from the
  text. Session strings in the digest stay single-line JSON literals.
- **The hook is an observer.** It prints nothing to stdout, never blocks or denies, always exits 0,
  and uses the standard library only. `summarizer.hook`, `store`, `redact`, `facts`, `events`,
  `labels` and `text` must not import NumPy, Torch or any model runtime.
- **Secrets are redacted before anything is written.** Do not commit session logs, summaries,
  seeds, model weights, training runs, held-out sets or `.env` files. Seeds, pairs built from them,
  held-out sets and other local-only material live under the ignored `private/summarizer/`.
- **Model.** EmbeddingGemma 2 (`google/embeddinggemma-2`), text encoder only, at the revision pinned
  in `src/summarizer/summarizer/embedder.py`. It is small: ask it for broad-strokes recognition (a
  few wide probes), not fine distinctions. It must run on CPU, Apple silicon, NVIDIA and AMD; never
  use float16 (the model card rules it out).
- **Training is for pattern recognition, not memorization.** Keep entity randomization, group-wise
  splits and held-out-family evaluation in `training/summarizer/`. AgentArmor's benchset is for
  measurement only; never train or calibrate on it.
- **Training data is synthetic, written from real seeds.** A seed is a masked excerpt of a real
  session; it shows what real text looks like and is never a training example. Seeds stay in
  `private/summarizer/seeds/`, untracked. AgentArmor's benchset and the corpora it was built from
  are never seeds. Every synthetic batch passes `training/summarizer/check_data.py` before it is
  used, and every campaign file passes `training/summarizer/check_campaigns.py` and
  `training/summarizer/check_evidence.py`. The user set this on 2026-11-09. Synthetic training,
  development and evaluation data with their proofs, receipts and manifests are tracked under
  `training/summarizer/data/`; data requests and protocols are in `training/summarizer/docs/`.
- **Checks.** From `src/summarizer/` with `PYTHONPATH=$PWD:../../training/summarizer`, run `pytest`;
  CPU tests use a bag-of-words stand-in for the model, and `SUMMARIZER_TEST_MODEL=1` runs the tests
  that load EmbeddingGemma 2. EmbeddingGemma renders and the fine-tuning tests need the training
  environment (`src/summarizer[train]`).
