# Working on Summarizer, AgentArmor's session core

Summarizer watches Claude Code and Codex sessions through their hooks and keeps a labelled summary
of each one for attack-detection models such as Kai. It began as its own repository, a proof of
concept (`github.com/min-mwei/Summarizer`). The user decided on 2026-11-13 that it is a core
part of AgentArmor and that the real work is here. These rules record decisions the code alone
does not show. The repository's own [AGENTS.md](../../AGENTS.md) applies as well.

- **Roles.** Three agents work here: Model Trainer, Model Reviewer and Data Engineer. They hand
  work over through the shared board, which is append-only: never edit another role's
  entry. The proof of concept's board holds the history through MR142.
- **Branches.** Do all development on `dev`. `main` only receives merges from `dev` that the user
  asked for; never commit to it directly. The user set this on 2026-11-09.
- **One of everything.** Nothing here is copied into another component, and nothing of another
  component is copied here. The monitor imports this package. The user set this on
  2026-11-13: no duplicated code; simple and robust.
- **The summarizer tags; it does not judge.** It marks entries and observables with indicators
  (label, confidence, basis) and a stronger model decides what is an attack. An entry can be
  suspicious without being one. Recall matters more than precision: do not add a gate that
  suppresses an indicator because it is common in benign sessions; give it a low confidence instead.
- **It watches what the agent does, not the quality of what it writes.** The concern is an
  agent compromising security by its actions: sending data out, suspicious file activity,
  reaching credentials, changing controls. Insecure code in the agent's own output is the coding
  agent's job, and tamper-proofing the event log is not planned. Detectors are tuned for recall;
  Kai is the low-false-positive stage. The user set this on 2026-11-10.
- **Where it runs.** For AgentArmor end to end, Summarizer and its model run inside an
  AgentArmor local server. That server may also run Kai-4B, and a decision model such as Kai-4B
  may instead run remotely in a cloud environment. The user set this on 2026-11-11.
- **It reads everything the agent reads and runs**: prompts, tool calls, tool results, instruction
  files. Anything dropped (clipped, unscanned, unreadable) must show up in `coverage`; never report
  a partial view as complete.
- **A summary is evidence for a decision about one tool call.** An attempt is never recorded as
  an outcome: an action's status says what a hook reported, and `returned` means a result came
  back, not that an effect happened. A later mention never rewrites who had named something at
  the time. Confidence is a ranking score, not a probability.
- **Artifacts are JSON Lines.** `events.jsonl`, `summary.jsonl`, probe sets, training data and
  session cases hold one record per line.
- **The search index is SQLite, and derived.** `index.sqlite` is written by the daemon alone
  and can be rebuilt from the logs; the hook never opens it. It holds what the log says,
  never what a model said of it, and says how far it has read. A hit in it is a lead: the
  ties a packet shows are made from summaries. The event log stays the source of truth.
  The user set this on 2026-11-13, asking for SQLite so that exact, full-text and later
  vector search are possible.
- **The summary is extractive.** The embedding model ranks, links and tags; it never writes text.
  Provenance (user, agent, lower-trust) comes from which hook delivered the text, never from the
  text. Session strings in the digest stay single-line JSON literals.
- **The hook is an observer.** It prints nothing to stdout, never blocks or denies, always exits 0,
  and uses the standard library only. `summarizer.hook`, `store`, `redact`, `facts`, `events`,
  `labels` and `text` must not import NumPy, Torch or any model runtime.
- **Secrets are redacted before anything is written.** Do not commit session logs, summaries,
  seeds, model weights, training runs or `.env` files.
- **Model.** EmbeddingGemma 2 (`google/embeddinggemma-2`), text encoder only, at the revision pinned
  in `summarizer/embedder.py`. It is small: ask it for broad-strokes recognition (a few wide
  probes), not fine distinctions. It must run on CPU, Apple silicon, NVIDIA and AMD; never use
  float16 (the model card rules it out).
- **Training is for pattern recognition, not memorization.** Keep entity randomization, group-wise
  splits and held-out-family evaluation in `training/summarizer/`. AgentArmor's benchset is for measurement only;
  never train or calibrate on it.
- **Training data is synthetic, written from real seeds.** A seed is a masked excerpt of a real
  session; it shows what real text looks like and is never a training example. Seeds stay in
  `training/summarizer/data/seeds/`, untracked. AgentArmor's benchset and the corpora it was built from are
  never seeds. Every synthetic batch passes `training/summarizer/check_data.py` before it is used. The user set
  this on 2026-11-09.
- **Checks.** Run `ruff format --check .`, `ruff check .` and `pytest` from the project
  environment. CPU tests use a bag-of-words stand-in for the model; set `SUMMARIZER_TEST_MODEL=1`
  to run the tests that load EmbeddingGemma 2.
