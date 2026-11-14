# Agent swarm

Three roles work on Summarizer, AgentArmor's session-monitor library (`src/summarizer/`, with its
training and measurement in `training/summarizer/`). Each runs as its own agent session with its
own model. They share nothing but the repositories: work is handed over through Git and through the
shared board, `../msgboard.md` beside this repository. This document moved here from the retired
Summarizer repository on 2026-11-13.

| Role | Runs as | Model | Reasoning effort |
|---|---|---|---|
| Model Trainer | Claude Code subagent `model-trainer` | Claude Fable 5.2 (`claude-fable-5-2`) | medium |
| Model Reviewer | Codex CLI | Astra (`gpt-6-astra`) | max |
| Data Engineer | Codex CLI | Daybreak Blue (`gpt-daybreak-blue-latest`) | medium by default |

The user assigns roles. A session holds one role at a time and takes it when the user says so; the
user can also make a main Claude Code session the Model Trainer by telling it so. Roles are
started by the user. An agent that needs another role's work leaves a request on the board; it
does not start that role's session itself.

## Starting a role

```sh
# Model Trainer: from a Claude Code session in this repository, ask for the subagent
#   "Use the model-trainer subagent to answer the review on the board"
# or run a whole session as the trainer:
claude --agent model-trainer

# Model Reviewer
codex exec -m gpt-6-astra -c model_reasoning_effort="max" --sandbox workspace-write \
  "You are the Model Reviewer. Read agent_swarm.md, then act on the newest ../msgboard.md entries addressed to you."

# Data Engineer
codex exec -m gpt-daybreak-blue-latest -c model_reasoning_effort="medium" --sandbox workspace-write \
  "You are the Data Engineer. Read agent_swarm.md, then act on the newest ../msgboard.md entries addressed to you."
```

The trainer's definition is `.claude/agents/model-trainer.md`, kept locally (this repository
ignores `.claude/`).
Codex reads [AGENTS.md](AGENTS.md) on its own, and the sections below are each Codex role's brief.
Daybreak Blue is OpenAI's model for authorized defensive security work and needs Trusted Access on
the account that runs it. The Data Engineer's effort can be raised for a hard batch
(`model_reasoning_effort="high"`); say so on the board when you do.

Data engineering uses Daybreak Blue only (the user, 2026-11-13). A blind read of data, such as a
qualification reader, is Daybreak Blue at `model_reasoning_effort="xhigh"`, launched by the tool
that runs the read, in an empty folder with its tools turned off; it is not a role and keeps no
session. Claude Opus and Sonnet are not used to write or blind-read data, because their safety
filters refuse synthetic credential and exfiltration content; nobody tries to get around them.

## Model Trainer

Owns the summarization runtime (`src/summarizer/`), the training pipeline
(`training/summarizer/*.py`), probe
calibration, fine-tuning runs, and every measurement reported from them.

- Answers the Model Reviewer's findings: reproduce each one, fix it or say why not, add a test
  that would have caught it, and report what was measured.
- Writes the data contracts (the formats in [training/summarizer/README.md](training/summarizer/README.md)) and asks the Data
  Engineer for data against them: which families, how many groups, what to hold out.
- Builds datasets from accepted batches, fine-tunes, calibrates on dev, and reports train, dev
  and held-out results side by side.
- Does not write training examples beyond the hand-written starter set, does not accept its own
  work, and never trains or calibrates on AgentArmor's benchset.

## Model Reviewer

Reviews design, code, data batches and measurement claims, and decides what is accepted.

- Reproduces a problem before reporting it, and says how. Ranks findings (P1 before P2) and
  names the files and lines.
- Checks that a claim is supported by what was actually measured, on which data and device.
- Accepts or returns each data batch: spot-checks labels and twins, looks for leakage between
  splits, and reads the `check_data.py` summary.
- Does not change runtime code, training code or data. Findings go on the board and the owning
  role makes the change.

## Data Engineer

Owns the synthetic training data (`training/summarizer/data/synthetic/`) and the handling of seeds.

- Exports seeds from real sessions with `training/summarizer/export_seeds.py`, when the user has agreed to it
  for those sessions. Seeds stay in `private/summarizer/seeds/`, which Git ignores.
- Writes new examples in the image of the seeds, in the formats the trainer documented: labelled
  templates for the probes, and session cases for `training/summarizer/context_eval.py`. Every example is new
  text. Entities are slots, never literal hosts or names.
- Keeps texts that match a probe (direct or quoted) and their non-matching look-alikes together
  in a group, labels by what the text contains rather than by whether it is an attack, spreads examples over many
  carrier families, and sets whole families aside as held-out when the request asks for it.
- Runs `training/summarizer/check_data.py` on each batch against the seeds it drew on and against AgentArmor's
  benchset, and posts the summary line with the batch.
- Does not train models or change runtime code, does not copy seed text into examples, does not
  commit seeds, and never uses AgentArmor's benchset or the corpora it was built from as seeds
  or as examples.

## How data moves

```text
real sessions ──export_seeds.py──> seeds (private)          Data Engineer, with the user's consent
seeds ──written in their image──> synthetic batch            Data Engineer
batch ──check_data.py──> summary on the board                Data Engineer
batch ──review──> accepted or returned                       Model Reviewer
accepted batches ──build_dataset.py, finetune.py──> model    Model Trainer
model ──evaluate.py, context_eval.py──> measurements         Model Trainer, then Model Reviewer
```

Training data is synthetic. Real data enters only as seeds, and a seed is never a training
example. [training/summarizer/README.md](training/summarizer/README.md) has the formats and the commands.

## The message board

The shared board, `../msgboard.md`, is how roles talk to each other. It is append-only, newest entry last.

Start each entry with this heading, taking the time and zone from `date`:

```text
## YYYY-MM-DD HH:MM ZONE, Role (agent, model): Title
```

Then say, in this order:

1. **To:** the role the entry is for.
2. What you did, with the commit it is in.
3. What you measured: the command, the data, the device and the numbers. Say what you did not
   run.
4. What you ask of the other role, concretely.
5. What remains open.

Rules:

- Never edit or delete another role's entry. Correct your own by appending.
- The board is a log of handoffs, not documentation. A fact that should outlive the exchange goes
  in the document that owns it ([README.md](README.md), [training/summarizer/README.md](training/summarizer/README.md),
  [AGENTS.md](AGENTS.md)) and the entry points there.
- Nothing private goes on the board: no secrets, no session logs, no seed text.
- Refer to code by path and commit, and to findings by the title the reviewer gave them.

All work happens on `dev`. Each role commits its own changes.
