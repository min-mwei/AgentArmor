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

Each line holds the exact request (`state` and `questions`), the packet's account of itself,
`summary_embedding`, the backend the summary was marked on, and `summary_detector`, the rules
and source that marked it. `sendable` is true only when a request was built; a trigger refused
for its size has `request: null` and says why in `refused`. This is the serving layout. The
same session can build a different request on another device or precision, because a mark near
a threshold moves, so keep `summary_embedding` with every render. Reruns on one backend have
matched where they were checked; that is an observation, not a guarantee. The output holds
session text; treat it like the session's own log.

`requests` shows a recorded session as it stands now, with every later event in it. For
training, calibration and test inputs use `render`, below, which shows each call as it stood
when it was proposed.

## Rendering scripted sessions

```sh
.venv/bin/agentarmor-monitor render scripts.jsonl --out rendered/
```

`render` turns sessions written as hook events into the requests the monitor would build for
them, and sends nothing. It is how inputs for Kai are made in the serving layout. Each line of
the input is one view of one root:

```json
{"root_id": "r-0412", "view_id": "r-0412-a", "agent": "claude_code",
 "rules": [{"id": "task", "tier": "allow", "description": "Calls the task needs."}],
 "events": [{"hook_event_name": "SessionStart", "source": "startup", "...": "..."}],
 "files": {"/srv/dev/proj/CLAUDE.md": "Run the tests before you commit."},
 "links": {"/srv/dev/proj/notes.txt": "/srv/dev/.ssh/id_rsa"}}
```

| Field | What it is |
|---|---|
| `root_id`, `view_id` | Yours. Carried onto every row; the pair is unique in a file. Views of one root stay in one split |
| `agent` | `claude_code` or `codex_code` |
| `rules` | The policy this view runs under, as in a policy file: one or more of `{id, tier, description}` with distinct ids. Rule texts, ids, their number, the tiers used and the order within a tier are free. The first line of the state, the empty `environment:`, the order of the tier headings and the "Policy interpretation" paragraph are fixed by the layout |
| `events` | The hook events, in order, as the agent delivers them (below). The first must carry a `transcript_path` |
| `files` | Optional. Instruction files as `{absolute path: text}`: what `InstructionsLoaded` and Codex's `SessionStart` find on disk. A file under a `.git` folder marks a repository root for Codex's search |
| `links` | Optional. `{absolute path: where it really leads}`, for a path that is a symbolic link |
| `codex_home` | Optional. The folder of Codex's global `AGENTS.md`, when the script has one |

Nothing else is accepted on a line: a label, a family or a note belongs in your own files,
joined by `root_id` and `view_id`, and never reaches a request.

**The events.** They are the hooks' own payloads. Every event has `hook_event_name`. Give each
the common fields the agents send: `transcript_path` (it must lie under a `.claude` or `.codex`
folder, as the agents' transcripts do; Summarizer reads `~` as the folder above that), `cwd` and
`permission_mode`. `session_id` may be left out of all of them. What each event adds:

| Event | Agents | Fields Summarizer reads |
|---|---|---|
| `SessionStart` | both | `source` (`startup`, `resume`, `clear`, `compact`). For Codex it also finds the `AGENTS.md` chain from `cwd` in `files` |
| `InstructionsLoaded` | Claude Code | `file_path`, `load_reason`; the text comes from `files` |
| `UserPromptSubmit` | both | `prompt`: the only thing that carries the user's authority |
| `PreToolUse` | both | `tool_name`, `tool_input`, `tool_use_id`: a proposed call. Every one gets a row in `calls.jsonl` |
| `PostToolUse` | both | the same three and `tool_response`: what the call returned, as lower-trust content. Codex reports a failed command here too |
| `PostToolUseFailure` | Claude Code | the same three and `error` |
| `PermissionRequest` | both | `tool_name`, `tool_input`: the agent asked the user |
| `PermissionDenied` | Claude Code | `tool_name`, `tool_input`, `tool_use_id`, `reason` |
| `Stop`, `SubagentStop` | both | `last_assistant_message` |
| `SubagentStart` | both | `agent_id`, `agent_type` |
| `PostCompact` | both | `compact_summary`, `trigger` |
| `ConfigChange` | Claude Code | `source`, `file_path` |

A `tool_use_id` ties a result to its call. A call first seen at its result has no observed
proposal and can never be a pre-action review. The agents' tool names and argument shapes are
their own (`Bash` with `command`; `Read`, `Write` and `Edit` with `file_path`; MCP tools as
`mcp__server__tool`); Summarizer reads a path, a command or a URL where it knows the tool and
the whole argument text otherwise.

**How it renders.** The events go one at a time through Summarizer's hook into a spool of the
render's own, and the daemon updates the summary after each. A request is built at the event
where its trigger first reaches the level, so it holds the session as it stood then: earlier
results, approvals and withdrawals, and nothing later. A proposed call is put through the
gate's own code with a client that sends nothing, and through the watcher's.

**Nothing comes from the machine.** A live hook asks the disk where a path really leads and
what an instruction file holds, and Summarizer falls back on the home of whoever runs it. In a
render the script answers all three. A path the script does not mention leads to itself (on a
Mac, `/home/...` otherwise resolves elsewhere and would be marked as a redirected path), a file
it does not hold could not be read (which is a gap in the session's record), and a script that
does not say where home is is refused. The user's own spool is never read or written.

**What it writes**, with mode 0600, and nothing at all if anything is wrong:

`requests.jsonl`, one row per trigger:

| Field | Meaning |
|---|---|
| `root_id`, `view_id`, `agent`, `rules` | From the script; `rules` is the ids in order and a SHA-256 of the rules as given |
| `event` | The event (from 1) at which the request was built. The session prefix ends there |
| `routes` | Which routes would send it: `watch` (the daemon, from `--level`, default low), `gate` (from `--gate-level`, default medium), or both when they build the same request |
| `key`, `trigger`, `review`, `layout`, `packet`, `summary_embedding`, `summary_detector` | As in a detection record (below) |
| `shown` | The entries the packet's text shows: type, the event each came from (`seq`), how many of its characters are shown, where in the event a piece of content sits (`ref`), and its trust (`user`, `agent`, or a lower-trust source). No text. Use it to check that the event holding a deciding premise is in the text |
| `coverage` | Summarizer's account of the session at that event: gaps, clipped and unscanned characters, redactions, files it could not read |
| `sendable`, `refused` | Whether a request was built; if not, why (`call_too_large`, `policy_too_large`) |
| `questions`, `request` | The questions by name, and the request exactly as sent: `model`, `state`, and each question's registered text. `packet.request_sha256` is the SHA-256 of `json.dumps(request, sort_keys=True)` |
| `pre_action_complete` | True when the trigger is a proposed call, the request was built, `packet.complete` holds and `review` is `pre_action`. A decision example needs all of these from a render. It is not a label and admits nothing |
| `same_request_as` | The key of an earlier trigger of the view that built the identical request |

`calls.jsonl`, one row per proposed call (`PreToolUse`), asked about or not:

| Field | Meaning |
|---|---|
| `event`, `call_id`, `call_sha256`, `tool` | Which call. The hash is of the tool name and arguments before redaction |
| `summarized`, `level`, `confidence`, `labels` | How Summarizer marked the call at its proposal. `summarized` is false when it made no entry of the call. A call the agent gave no `tool_use_id` is found as the action its event produced: the watcher can still ask about it, and the gate, which needs the id, cannot check it (`why: event`) |
| `watch` | `asked`, with the request's `request_sha256`, or `why` not (`below_level`, `no_summary`), or `refused` for size |
| `gate` | `asked`, with `request_sha256` and `an_allow`: what the gate makes of an "allow" from Kai, `stands`, or `incomplete` or `moved_on` (the gate then stops the call by default). Otherwise `outcome`: `abstain` (`why: below_level`: the call runs under the agent's own permission flow and Kai is never asked) or `undecided` with `why` (`coverage`, `no_summary`, `call_too_large`, `policy_too_large`, `event`), which the gate turns into a denial unless it runs with `--on-error abstain` |
| `routes_agree` | Whether the watcher and the gate built the same request, when both asked |
| `pre_action_complete` | That of the call's request, or null when neither route built one |

Count from `calls.jsonl`. A call neither route asks about is in that file and in no other, and
a monitor leaves no record of a call below the gate's level. A later, retrospective request
about a call does not change what happened at its proposal.

`manifest.json` names what rendered (a hash of this package's source, the layout, hashes of the
questions and of the policy interpretation), what the summaries say embedded and marked them,
the options, the SHA-256 of the input, counts in which every proposed call appears once, and
the SHA-256 and row count of each output.

Every view is rendered twice and the two must be equal, or nothing is written. That catches
what varies inside one process. To check a model across processes or devices, render again
and compare the output hashes in the two manifests. Events are fed one at a time; a live
daemon may embed several in one batch, and a score can then differ in its last digits. A
smaller `--max-chars` builds smaller packets, which may drop the premise a decision needs:
check `shown` and `packet` again, since a smaller render inherits nothing from a larger one.
`render` counts characters; count tokens with Kai's own tokenizer on `request.state`. A row is
the first request the monitor builds. If the service refuses a state as too large, the monitor
rebuilds it from half the packet and sends that, which `render` does not show: a row whose
state is over the service's limit is not what would be served, and is to be left out or
rendered again smaller, not used as it is.

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
| `packet` | Size and SHA-256 of the packet; `state_chars`; whether the call was verified; Summarizer's own account (`ready`, `dropped` (lines that found no room, by section), `key_dropped`, `key_clipped`, `unresolved_conditions`, `scanned_all_captured`, `later_events`); `request_sha256`, the identifier of the final request (canonical JSON, not wire bytes); the call's `call_source`, `call_chars`, `call_chars_not_shown` and `call_redactions` (the last two are null for a `representation`: unknown); and `complete`: the packet was ready and the call's own arguments were shown whole |
| `review` | `pre_action` when the hooks delivered the call's proposal, nothing has been reported to have become of it, and the summary held nothing from after it; otherwise `retrospective`. A call first seen at its result or failure is always retrospective (`packet.proposal_observed` is false): its outcome is already in the packet. Only pre-action records are evidence about what could be known in time. This is a separate property from `packet.complete`, which is about how much was shown |
| `requests_sent` | How many requests this trigger actually put to Kai: 0 when its answer was one already given for the same request, more than 1 when a larger request was refused first |
| `same_request_as` | Present only when this record's answer was given for another trigger's identical request in the same summary: that trigger's key. Never set by a refusal, and never the record's own key |
| `layout` | What Kai was given, by name: the state's layout (`agentarmor-state/v1`) and the packet's schema (`summarizer/v2`). A change to either is a change to Kai's input |
| `summary_embedding` | What the summary says embedded it: backend, model, revision, device, precision, vector size, probe version and whether the probes are calibrated. Copied from the summary the request was built from, which for the daemon is the copy queued with the trigger, never from what is running later. It sits beside the request, never inside it, so request ids do not depend on it. `null` when no summary was read or the summary does not say. It traces what was reported and pins neither weights nor probes |
| `summary_detector` | What the summary says marked it: a version and hashes of Summarizer's wording rules, of its priors and levels, and of its source. The same session marked under other rules can build a different packet. Copied from the same summary as `summary_embedding`, and like it beside the request, never inside it. `null` when the summary does not say, as with summaries made before Summarizer named its detectors; a hash the summary gives as unknown stays `null` |
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
