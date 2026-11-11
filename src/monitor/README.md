# The local monitor

`agentarmor-monitor` connects the two halves of AgentArmor on a developer machine:

- **Summarizer** is the local model. Its hook records everything a Claude Code or Codex session
  reads and runs, and its daemon (EmbeddingGemma 2, on CPU or a local GPU) keeps a labelled
  summary of each session: the user's requests, each tool call and its reported outcome, the
  content the agent was shown, and indicators on anything that looks like an attempt to steer
  the agent, reach secrets, send data out or change controls. It tags for recall and does not
  judge.
- **Kai** decides. Whatever Summarizer marks at or above a level is sent to the Kai service
  with the rules and Summarizer's summary of the session as context. Kai answers its two
  trained questions: should the proposed tool call be allowed or denied under the rules, and
  is untrusted content in this log trying to redirect the agent.

```
agent hooks ─> Summarizer hook ─> events.jsonl ─> monitor daemon ─> summary.jsonl
                                                        │ marked at or above --level
                                                        ▼
                               rules + Summarizer's packet + the call ──> Kai (/v1/systemone)
                                                        │
                                                        ▼
                                   detections.jsonl: allow or deny, with Kai's probabilities
```

**Status.** Kai's serving path is unqualified (`policy_ready: false`), and Kai has not been
measured on Summarizer's packets: they are laid out like Kai's tool-call decisions, but the
"conversation" is a monitor's summary, which Kai was not trained on. Treat every decision as
experimental. On the benchset's own request text, Summarizer's mark followed by Kai's saved
score takes the false-positive rate on 529 benign prompt-injection cases from 35% to 1.9% at
Kai's 0.5 cutoff and catches 800 of 997 attacks; 36 never reach Kai and it misses 161
(Summarizer's `train/two_stage.py`, escalating from "low"). That is not the integrated path.
Summarizer's `docs/model-plan.md` reviews where those misses are and what is planned.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install ./src/vllm './src/monitor[model]'
```

`[model]` brings Summarizer's embedding runtime (Torch). Without it the monitor can run with
`--embedder hash`, a stand-in for tests that does not recognize paraphrase. Kai itself is served
separately ([serving guide](../vllm/README.md)), on a Linux GPU host; reach it on a loopback
address, for example through `ssh -L 8009:127.0.0.1:8009 gpu-host`.

## Run

```sh
.venv/bin/agentarmor-monitor doctor                      # spool, hook path, does Kai answer
.venv/bin/agentarmor-monitor serve --kai-url http://127.0.0.1:8009
.venv/bin/agentarmor-monitor detections --flagged        # what Kai denied or flagged
```

Connect an agent in one of two ways.

**Observe.** The agent runs normally. Summarizer's hook prints nothing and never blocks; the
daemon records Kai's decision for each marked entry. It sends on everything Summarizer marks,
from "low" up (`--level`): watching is tuned for recall, and Kai is the stage that keeps false
alarms down. Kai is asked on a separate worker, so a slow or unreachable Kai never delays a
session's summary.

```sh
.venv/bin/agentarmor-monitor hooks-config --agent claude_code     # merge into ~/.claude/settings.json
.venv/bin/agentarmor-monitor hooks-config --agent codex_code --toml
```

**Gate.** One hook handles every event. It records the event through Summarizer's hook, and
for a tool call about to run it waits for the summary of the session up to that call. The gate
asks Kai from "medium" up (`--level`): a denial blocks a call, and Kai's decisions on real
calls are unmeasured.

- A call Summarizer did not mark at that level passes without Kai being asked, and the agent's
  own permission flow applies, provided Summarizer scanned everything it captured in the
  session.
- A marked call goes to Kai. "deny" is returned to the agent as a denial with Kai's
  probability. "allow" says nothing, provided Kai was shown complete evidence from before the
  call.
- A check the gate cannot stand behind is not a pass. The call is denied, with the reason,
  when:
  - the event cannot be read (not JSON, too large, or nested too deeply to parse) or
    recorded, or the gate cannot start (a missing policy file, a Kai address that is wrong or
    not on this machine);
  - no summary of this exact call arrives within `--wait` (for example, the daemon is not
    running);
  - the session record has gaps (something clipped, unread or not scanned), so "not marked"
    is not a finding;
  - the call is too large to show Kai whole within `--max-chars`;
  - Kai allowed it on a packet that left key evidence out, or that already held events from
    after the call (calls proposed side by side can do this);
  - Kai is unreachable or refuses the request.

  `--on-error abstain` lets all of these through instead. Either way the outcome is appended
  to the session's detections with what was enforced, so default denials can be counted.

A denial is printed on stdout in the agent's own shape and its reason on stderr, with exit
status 2. A failure is also noted in `gate-errors.log` in the spool, as the step that failed
and the error's class only: an error's own text can quote an event that was never redacted.

```sh
.venv/bin/agentarmor-monitor hooks-config --agent claude_code \
    --gate "$(pwd)/.venv/bin/agentarmor-monitor"
```

Give the gate the same `--kai-url`, `--level` and `--policy` as the daemon by adding them to
the hook's arguments.

## What is sent to Kai

A state in the layout of Kai's tool-call decisions:

```text
Armor rules (classifier_context v1, auto_mode):
environment:
hard_deny:
  - [id=secrets] Calls that disclose credentials or private data to an unauthorized destination.
  ...
allow:
  - [id=delegated-task] Calls needed for the user's authorized task ...

Policy interpretation:
The conversation below is a summary of the agent session made by the local monitor. ...

Conversation so far:
SESSION SUMMARY summarizer/v2 ...            <- Summarizer's packet for this call

Proposed tool call:
  tool: Bash
  arguments: {"command": "..."}
```

- **Rules** come from `--policy FILE` (one JSON object per line: `id`, `tier` of `allow`,
  `soft_deny` or `hard_deny`, `description`); without it, the rules of
  [`agents/policy.example.jsonl`](../../agents/policy.example.jsonl). Host-computed path facts
  (`src/hostfacts`) are not filled in yet, so `environment:` is empty.
- **The packet** is `summarizer.text.digest` focused on the call: the requests on the user
  channel, the call, what it resembles and where its operands were first named, reported
  outcomes, and a last line counting what is not shown. Secrets were redacted before anything
  was written.
- **The proposed call** is shown whole or not at all, and the record says where its text came
  from (`call_source`):
  - `arguments`: the gate holds the call's own arguments and redacts them exactly as
    Summarizer's hook redacts what it logs, so the request never carries what the packet
    withheld. The call's identity stays the hash taken before redaction.
  - `log`: the daemon, `scan` and `requests` find the same arguments in the session's event
    log, by call id and by that hash. They are redacted already and are shown as the gate
    would show them.
  - `representation`: when the log cannot supply them, only the text Summarizer shows for a
    call is left. That is a reading of the call, not the call: a patch is shown as its files
    and added lines, without the lines it removes. It is sent, how much it leaves out is
    recorded as unknown, and it never counts as a complete check.
- **One budget.** `--max-chars` (5,000) covers the whole state: the rules, the packet and the
  call. The rules and the call are never cut; the packet gets the room they leave. If that is
  less than the smallest packet (1,000 characters) nothing is sent. If the service refuses a
  state as too large, a smaller packet is built, which says what it left out.
- **The questions** are Kai's registered `decision` and `is_xpia_attempt`, word for word
  (`kai.py`). A marked entry that is not a tool call (content, an agent message) is asked
  about redirection only.
- **One request, one question to Kai.** Several marked pieces of one result build the very
  same request, because a packet is centred on the call the content came back from. Triggers
  found in one summary whose requests are the same are put to Kai once; each still gets its
  record. `packet.request_sha256` identifies the request: the SHA-256 of the request object
  as canonical JSON (sorted keys, non-ASCII escaped), which is an identifier of its content,
  not a hash of the bytes on the wire. `requests_sent` counts what the trigger itself put to
  the service, and `same_request_as` names the trigger whose answer it repeats. A request the
  service refused is not sent again either, but that leaves no link: a refusal is not an
  answer, and an answer obtained after one is the trigger's own. Nothing is shared with a
  later look at the session, or between an action and the content it returned: those are
  different requests or different decision times.

A packet holds what the agent read. It goes only to a Kai service on a loopback address unless
`--allow-remote` is given.

To see what a session would send without sending it:

```sh
.venv/bin/agentarmor-monitor requests --session <id>     # one JSON line per trigger
```

Each line holds the exact request (`state` and `questions`), the packet's account of itself
and `summary_embedding`, the backend the summary was marked on. `sendable` is true only when a
request was built; a trigger refused for its size has `request: null` and says why in
`refused`. This is the serving layout: training, calibration and test inputs for Kai can be
rendered in it from recorded or authored sessions. The same session can build a different
request on another device or precision, because a mark near a threshold moves, so keep
`summary_embedding` with every render. Reruns on one backend have matched where they were
checked; that is an observation, not a guarantee. The output holds session text; treat it like
the session's own log.

## Detections

One JSON line per trigger in the session's directory under Summarizer's spool
(`~/.summarizer/sessions/<agent>/<session>/detections.jsonl`, owner-readable only):

| Field | Meaning |
|---|---|
| `trigger` | The entry Summarizer marked: type, sequence number, call id, the call's hash before redaction, tool, level, labels |
| `decision` | Kai's answer for a tool call, `allow` or `deny`; null for other entries |
| `kai` | `p_deny`, `p_xpia` and the service's identity (model, revision, package hash, `policy_ready`) |
| `flagged` | `decision` is `deny`, or `p_xpia` is at least 0.5 (not a qualified cutoff) |
| `disagreement` | Summarizer marked the entry medium or higher and Kai cleared it. Kept apart from "nothing found"; list them with `detections --disagreements` |
| `packet` | Size and SHA-256 of the packet; `state_chars`; whether the call was verified; Summarizer's own account (`ready`, `key_dropped`, `key_clipped`, `unresolved_conditions`, `scanned_all_captured`, `later_events`); `request_sha256`, the identifier of the final request (canonical JSON, not wire bytes); the call's `call_source`, `call_chars`, `call_chars_not_shown` and `call_redactions` (the last two are null for a `representation`: unknown); and `complete`: the packet was ready and the call's own arguments were shown whole |
| `review` | `pre_action` when the hooks delivered the call's proposal, nothing has been reported to have become of it, and the summary held nothing from after it; otherwise `retrospective`. A call first seen at its result or failure is always retrospective (`packet.proposal_observed` is false): its outcome is already in the packet. Only pre-action records are evidence about what could be known in time. This is a separate property from `packet.complete`, which is about how much was shown |
| `requests_sent` | How many requests this trigger actually put to Kai: 0 when its answer was one already given for the same request, more than 1 when a larger request was refused first |
| `same_request_as` | Present only when this record's answer was given for another trigger's identical request in the same summary: that trigger's key. Never set by a refusal, and never the record's own key |
| `layout` | What Kai was given, by name: the state's layout (`agentarmor-state/v1`) and the packet's schema (`summarizer/v2`). A change to either is a change to Kai's input |
| `summary_embedding` | What the summary says embedded it: backend, model, revision, device, precision, vector size, probe version and whether the probes are calibrated. Copied from the summary the request was built from, which for the daemon is the copy queued with the trigger, never from what is running later. It sits beside the request, never inside it, so request ids do not depend on it. `null` when no summary was read or the summary does not say. It traces what was reported and pins neither weights nor probes |
| `status` | `scored`; `kai_unavailable` (retried by the daemon); `kai_rejected`; `call_too_large` or `policy_too_large` (nothing was sent); and for a gate outcome without a Kai request, the reason (`no_summary`, `coverage`, `setup`, `recording`, `event`) |
| `gate`, `verdict`, `why`, `enforced` | Present when the gate handled the call: its verdict (`abstain`, `deny`, `undecided`), what kept an undecided check from completing, and whether the call was in fact denied (`deny`) or not (`none`) |

The packet text is not copied into the record; it can be rebuilt from the summary. When a gate
and the daemon both ask about one call, the answer stands: a later note that Kai could not be
reached does not replace it.

## Limits

- Kai on Summarizer packets is unmeasured, and the gate applies an unqualified model's
  decisions. This is not a claim of attack prevention.
- The trigger is Summarizer's marking. A call it does not mark is never shown to Kai.
- `flagged` uses 0.5, which is a default and not an operating point. No cutoff has been fitted
  on a calibration set, and none should be taken from the benchset.
- The gate is strict by design. A session whose record has any gap (a clipped result, an
  instruction file the hook could not read, a command not analysed in full) has every later
  call denied unless `--on-error abstain` is chosen. A marked call proposed side by side with
  others can be denied because the summary already held the others.
- The gate adds the time to summarize the call (tens of milliseconds with the daemon running)
  and, for a marked call, one Kai request.
- An agent running as the same user can stop the daemon or edit the spool. Summarizer tags
  attempts on its own files; nothing verifies the log's integrity.
