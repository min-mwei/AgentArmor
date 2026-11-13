# The local monitor

`agentarmor-monitor serve` is **AgentArmor's local server**. Summarizer and its model run
inside it, always, and it holds the rules. The decision model, Kai, is either loaded by this
server or reached at another address, which can be a host in a cloud. The two halves:

- **Summarizer** is the local model. Its hook records everything a Claude Code or Codex session
  reads and runs, and its daemon (EmbeddingGemma 2, on CPU or a local GPU) keeps a labelled
  summary of each session: the user's requests, each tool call and its reported outcome, the
  content the agent was shown, and indicators on anything that looks like an attempt to steer
  the agent, reach secrets, send data out or change controls. It tags for recall and does not
  judge.
- **Kai** decides. Whatever Summarizer marks at or above a level is put to Kai with the rules
  and Summarizer's summary of the session as context. Kai answers its two trained questions:
  should the proposed tool call be allowed or denied under the rules, and is untrusted
  content in this log trying to redirect the agent.

```
                 ┌───────────────────── AgentArmor local server ─────────────────────┐
agent hooks ─> spool ─> Summarizer's daemon and model ─> summary.jsonl               │
(observer or gate)        │ marked at or above --level                               │
                 │        ▼                                                          │
                 │   the server's rules + Summarizer's packet + the call as logged   │
gate: "decide    │        │                                                          │
 this call" ────>│  127.0.0.1:8010  /health  /v1/decide        Kai, loaded here      │
                 │        │                                    or ──https + token──> │ Kai elsewhere
                 └────────┼──────────────────────────────────────────────────────────┘
                          ▼
           detections.jsonl: allow or deny, with Kai's probabilities
```

**Who owns what.** The server owns everything a decision rests on: the rules, its record of
each session, the request the decision model reads, the way to that model and the check that
what answered is the model the operator configured. A gate owns one thing, which is what to
do when it gets no verdict (`--on-error`). It names a call and is told the outcome. It holds
no rules, builds no request, has no address of a decision model and no credential for one.

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
.venv/bin/pip install './src/monitor[model]'
```

`[model]` brings Summarizer's embedding runtime (Torch). Without it the server can run with
`--embedder hash`, a stand-in for tests that does not recognize paraphrase. A fine-tuned
EmbeddingGemma 2 directory (`--model DIR`) is loaded with the probes that were calibrated
for it: its own `probes.jsonl`, or the file given with `--probes`. The built-in probe
numbers fit the pinned base model only, so a model directory without probes is refused
unless `--uncalibrated` is given; it then ranks and links and gives none of the three
probe labels, and `/health` says `calibrated: false`. To load Kai in
the server, install `./src/vllm` with its `serve` extra as well, on a host its engine
supports ([serving guide](../vllm/README.md): a Linux GPU host today). A server that reaches
Kai at another address, or runs Summarizer alone, needs nothing of `src/vllm`.

## Run

```sh
.venv/bin/agentarmor-monitor serve                                       # Summarizer only
.venv/bin/agentarmor-monitor serve --kai-package /path/to/kai-export     # Kai loaded here
.venv/bin/agentarmor-monitor serve --kai-url https://kai.example.com \
    --kai-package-sha256 "$PACKAGE" \
    --allow-remote --kai-token-file ~/.config/agentarmor/kai.token       # Kai in a cloud
.venv/bin/agentarmor-monitor doctor                      # spool, hook path, what the server says
.venv/bin/agentarmor-monitor detections --flagged        # what Kai denied or flagged
```

**Where Kai is.** One of three, chosen when the server starts. Summarizer watches and marks
in all three, and never waits for Kai.

| | Summarizer only | Kai loaded by the server | Kai at another address |
|---|---|---|---|
| Start with | no Kai option | `--kai-package DIR` or `--kai-revision SHA` | `--kai-url URL --kai-package-sha256 HASH` |
| It starts when | the port is free and the rules load | and Kai's own loader accepts the package | and the address is one it may use (below). If that address answers, it must be the named package |
| It refuses to start when | the port is taken, the rules do not load | the package is not the one `--kai-package-sha256` names, if given | no package is named; something else answers there |
| It starts all the same when | | | the address does not answer yet: it says so, and asks again later |
| A marked entry it watches | recorded by Summarizer; nobody is asked | asked, one request at a time | asked; with no answer it is recorded once and tried again |
| A gate's unmarked call | passes, in a session scanned whole | the same | the same |
| A gate's marked call | undecided: no decision model | Kai's verdict | Kai's verdict, or undecided when Kai does not answer |
| What answers is not the configured model | | cannot happen: the model is the one loaded | its answer is not used: undecided, and `/health` names what differs |
| `/health` → `decision_model.at` | `none` | `local` | `remote`, with `reachable` and `identity` |

A server that loads Kai itself does not have to keep it in its own process: that is how it is
built today, and a supervised worker behind the same port would serve as well. Kai's engine
runs on a supported Linux GPU host; on other hosts use the first or the third column.

**The decision model is the one configured, or its answer is not used.** An answer is read
as Kai's only when the reply names the model, the readout and the package the operator
configured (`kai-4b`, `pointer`, and the package hash). For a Kai at another address the
hash is `--kai-package-sha256`; for one loaded here it is the package that was loaded. Every
answer is checked, not only the first: what is behind an address can change between two
requests. A hash a service reports is its own claim and proves nothing by itself; it counts
because it equals what the operator set down. A service that says it is ready for use as a
policy is recorded as claiming so (`claims_policy_ready`), and nothing here becomes ready by
it. A different decision model would need its own adapter, questions and measurements: Kai's
probabilities are not read out of whatever another model returns.

**A Kai that is not on this machine.** A request holds what the agent read, with secrets
redacted and nothing else removed. So:

- it needs `--allow-remote`, which is the operator saying that session text may leave;
- it goes over `https` only. Plain `http` to a host that is not loopback is refused, and
  there is no option to allow it. Certificates are verified as the system verifies them;
- the service's bearer token comes from `--kai-token-file`, or from `$AGENTARMOR_KAI_TOKEN`
  when the service is not on this machine. A token lying in the environment is never sent to
  an address on this machine. It is sent only as a header to the one configured address, and
  never printed, logged or reported;
- no redirect is followed, so neither a request nor the token moves on to an address the
  service points at;
- a request for an address on this machine is sent to this machine: `http_proxy` and its
  kin in the environment are not used for it.

**What the server answers, on a loopback port** (`--port`, 8010 by default):

| | |
|---|---|
| `GET /health` | What is loaded (`summarizer`), how far behind the sessions it is (`sessions`: held, behind, waiting for Kai), where the decision model is, what was configured, whether it answers and is that model (`decision_model`), and what decisions rest on (`decisions`: the rules by id and hash, the gate's level, the budget, the layout). `policy_ready` is false whatever the model says. No session text and no secret |
| `POST /v1/decide` | A gate's question about one proposed call, and the server's receipt (below) |
| `POST /v1/systemone` | Off unless the server was started with `--model-api`: the decision model's own interface, for a caller that brings its own state |

The port has no authentication. Any program on this machine, of any user, can ask it, and
a loopback address is not a statement about who is asking. That is why the default is this
narrow. Through `/v1/decide` such a program can learn a verdict and some hashes about a call
that is already in the server's own log, and can cause one request to the decision model
that the server itself writes. It cannot have any text of its own sent anywhere. With
`--model-api` it can: its text goes to the decision model, with the server's token when the
model is in a cloud. Turn that on only where every local program may do so.

The server decides at most `--at-once` calls at the same time (4), and handles 16
connections at once. A caller beyond either is told so or closed at once, and a gate never
takes that for a pass.

Connect an agent in one of two ways.

**Observe.** The agent runs normally. Summarizer's hook prints nothing and never blocks; the
server records Kai's decision for each marked entry. It sends on everything Summarizer
marks, from "low" up (`--level`): watching is tuned for recall, and Kai is the stage that
keeps false alarms down. Kai is asked on a separate worker, so a slow or unreachable Kai
never delays a session's summary.

```sh
.venv/bin/agentarmor-monitor hooks-config --agent claude_code     # merge into ~/.claude/settings.json
.venv/bin/agentarmor-monitor hooks-config --agent codex_code --toml
```

**Gate.** One hook handles every event. It records the event through Summarizer's hook, and
for a tool call about to run it asks the local server to decide.

```sh
.venv/bin/agentarmor-monitor hooks-config --agent claude_code \
    --gate "$(pwd)/.venv/bin/agentarmor-monitor"
```

**What holds when the gate itself cannot run.** The gate denies on every error it can see.
It cannot see its own executable missing, a start that fails before it runs, or the client
ending it at the client's deadline. What happens then is the client's to say:

- **Claude Code** stops the call when the handler says so: the `PreToolUse` handler that
  `hooks-config --gate` prints carries `"onFailure": "block"`. It needs Claude Code 2.1.295
  or later. `hooks-config` reads the version of the `claude` on this machine and refuses
  to print a gate for an older one, unless `--no-failure-block` is given: the gate then
  stops only the calls it is run for and answers in time. Only that one handler has it.
  Every other event is recorded and never blocks.
- **Codex** has no such setting. A gate that cannot start, or that Codex ends at its own
  deadline, does not stop the call, and input sent to a process that is already running
  (`write_stdin`) is not put to a hook at all. A Codex gate is a check on the calls it
  sees, not a boundary. Keep Codex's own approvals and sandbox on.

In both clients another hook, or the permission step, can change a call's arguments after
the gate has answered for the call it was shown. Do not run the gate beside a hook that
rewrites tool input. Measured on Claude Code 2.1.323 and Codex CLI 0.168.0 with harmless
writes; see Summarizer's `docs/agentarmor-cli-protection-review.md` (MR117).

The gate's own options are `--server` (the local server's address, which must be on this
machine), `--timeout` and `--on-error`. The level a call must be marked at, the rules, the
budget and how long to wait for a summary are the server's (`--gate-level`, `--policy`,
`--max-chars`, `--wait`), so a gate cannot run under other rules than the server it asks. A
gate that is started with one of the options it used to have (a policy file, a level, a Kai
address, a token file) has not started: none of them is used, the call it guards is stopped
with that reason, and every other event is still recorded.

*What a gate sends* is six strings: the agent, the session id, the call id, the SHA-256 of
the call it holds (tool name and arguments, as Summarizer's hook hashes them), a name it
makes up for this asking (the attempt), and its `--on-error` setting, which the server keeps
with its record and decides nothing by. Not the arguments, no rules, no text. The server
finds the call in its own log of the session and decides only about a call with that hash.

*What comes back* is a receipt (`agentarmor-decision/v1`):

| Field | What it says |
|---|---|
| `verdict` | `deny`: the decision model denied the call. `abstain`: nothing stands against it, so the agent's own permission flow applies. `undecided`: the check could not be completed |
| `why` | For `undecided`, what kept it from being completed (the list below) |
| `p_deny` | The decision model's probability of deny, when it was asked |
| `bound` | What the verdict is about and rests on: the agent, session, call id, call hash and attempt asked about; `evidence` (how many events and bytes of the session's log had been read, whether all of it was scanned, and a hash of the workspace); `routing` (`eligible`: whether the call was marked at the threshold; `requests_sent`: how many requests were handed to the transport, which is 0 for a state refused here for its size in characters or in bytes on the wire; `requests`: what became of them, each counted apart (`built`, `refused_before_transport`, `transport_attempts`, `responses`, `answers_admitted`), where an attempt is not a delivery; the level, the threshold and the reason); `applicable` (`before_routing`: whether the proposal still stood when the log was read to its end before any routing: no request or answer from the user and no report of the call's outcome after it; with the count of events after it, how far the summary had read (`summary_log_bytes`), how far this reading got (`to_log_bytes`) and whether that was the log's end (`to_the_end`); a log that could not be read to its end is `log_unread`, never a pass); `revalidated` (for an allow: how far the log was read again when the answer came, and whether that was its end); `request_sha256`, the last request that was built, given only if that request was handed to the transport (null otherwise), and `complete`, which is null unless something came back for that same request: a response to an earlier, larger request that was then retried smaller says nothing of the retry; `last_request` gives that one request's own disposition (`transport_attempts`, `responded`, `admitted`); `policy` (the rules by id, and a hash of all of them) and `layout`; `decision_model` (the model, readout and package that answered); `complete` and `review`; and what embedded and marked the summary. Where the decision model was not asked, as for a call nothing marks, or no request was sent, the request, the model and `complete` are null: nothing is made up to look like a request. A gate takes a receipt only if every field it reads has the shape it is read in |
| `policy_ready` | false, always |

A receipt holds no session text. A gate uses one only if it answers the very asking it made
about the very call it holds.

**Three things, recorded by whoever knows them.**

1. *The decision as issued*, by the server: a record with `stage: issued` for every question
   it answers, a call nothing marks included (`status: unmarked`), so the calls that passed
   without Kai can be counted. It has the verdict, the attempt, the call's id and hash, the
   hash of the rules, the evidence it read, and `declared_on_error`: what the gate said it
   would do without a verdict. It never says what was answered. The server cannot know.
2. *What the gate answered*, by the gate: a record with `stage: answered` for every call it
   handles, with `answered` (`deny` or `none`), `would` (what it answers when it is not in
   shadow), `mode`, whether a receipt came, what that receipt was bound to, and how long
   the answer took (`elapsed_ms`). A gate that stopped waiting has answered, deny or nothing
   as it was set to, whatever the server says later: the late answer is on record as issued.
   This is the gate's answer and no more. Whether the agent received it in time and acted
   on it is not known at the gate (`delivery: unknown`): a client that ends the hook at its
   own deadline runs the call, and so does one in which another hook changes the call
   afterwards. **Do not count enforcement from these records alone.**
3. *What became of the call*, by the hooks: the result, failure or refusal that the session's
   log reports afterwards. A call that was let through is not thereby a call that ran.

The attempt and the call's ids tie the first two together, and an `observed` record ties
the third to them. What a gate answered is counted from the `answered` records. Whether a
call was in fact stopped is known only from what was reported next: a deny followed by a
result (`ran_after_deny`) was not.

The server asks Kai from "medium" up (`--gate-level`): a denial blocks a call, and Kai's
decisions on real calls are unmeasured.

- A call Summarizer did not mark at that level passes without Kai being asked, and the agent's
  own permission flow applies, provided Summarizer scanned everything it captured in the
  session
  and the proposal still stands: the session's log holds no request or answer from the
  user and no report of this call's outcome after it. A fresh summary of the right call is
  not enough for that, since it may already hold the user taking the call back.
- A marked call goes to Kai. "deny" is returned to the agent as a denial with Kai's
  probability. "allow" says nothing, provided Kai was shown complete evidence from before the
  call and the user has not spoken since that evidence was read.
- A check nobody can stand behind is not a pass. The call is denied, with the reason, when:
  - the event cannot be read (not JSON, too large, or nested too deeply to parse) or
    recorded, or the gate cannot start (a server address that is wrong or not on this
    machine);
  - the local server does not answer, answers about another call, or is deciding as many
    calls as it takes at once;
  - no summary of this exact call arrives within the server's `--wait` (the call is not in
    its log, or is there with other arguments);
  - the session record has gaps (something clipped, unread or not scanned), so "not marked"
    is not a finding;
  - no decision model is behind the server, it does not answer, it refuses the request, or
    what answers is not the model that was configured;
  - the call is too large to show Kai whole within `--max-chars`;
  - Kai allowed it on a packet that left key evidence out, or that already held events from
    after the call (calls proposed side by side can do this);
  - Kai allowed it, and the allow no longer applied when it came. Every byte the session
    logged while the answer was awaited is read again: the user made a request or answered a
    question (what the user said then is not in what Kai read), the hooks already report the
    call as run, failed or refused, or the log could not be read to its end, as when an event
    is half written or the disk filled up. Another call proposed meanwhile changes nothing.

  `--on-error abstain` lets all of these through instead. Either way the outcome is appended
  to the session's detections with what the gate answered, so default denials can be counted.

**What the agent reported, and when.** When a result, a failure or a refusal of a call
arrives, the gate appends a record with `stage: observed`: what was reported, the answer
it is tied to, and `order`:

- `after_answer`: an answer was on record that had been made before the report came.
  `ran_after_deny` is true when a result came back for a call the gate answered `deny`
  for: that call ran all the same.
- `no_answer`: no answer for that call was on record. The report is kept as it is. If
  no answer is ever tied to it, the gate was never run for that call.
- `before_answer`: written when the answer is recorded, for each report that had come
  before the answer was made. A result among them is `ran_before_answer`: the client did
  not wait, and whatever the gate then answered came too late for that call.
- `unknown`: an answer and the report are both on record, and when each arrived does not
  agree with where each landed, or they arrived in the same millisecond. Nothing is made
  of such a report: it neither ran after a deny nor before the answer.

A report is timed when it comes, before it waits for anything, and an answer when it is
made.

A report and an answer for one call are written by two hook processes. Each takes a lock
beside the session's detections for its read-and-append, so that neither misses the other;
a gate does not wait for that lock for more than a second, and each record says whether it
was written under it (`locked`). The lock orders the appends and not the arrivals: a
report that came first can wait behind an answer made later. So `gates` does not rely on
the lock, nor on what a record says of itself. It puts each report beside the answers of
the same agent, session and call, and takes an order from one thing only: their places in
the file and their times agreeing, with the times different. Equal times, a time that
contradicts the place, and a missing time say nothing, under the lock as without it, and
the report is counted as `reported_in_an_order_not_known`.

A failure or a refusal after a deny is left as reported: either can be how an agent
reports the denial. A reported result is not proof of any effect. A gate that could not
run at all (a missing executable, a client's deadline) leaves no record, so the gate's
records are not a count of every call: `gates` counts what gates answered, and
`elapsed_ms` starts when the gate has started, not when the client started it.

**Shadow.** `serve --shadow` issues every decision as a shadow one, and puts every marked
call to Kai (the gate's level is `low` unless `--gate-level` names another).
`hooks-config --gate PATH --shadow` prints a gate that asks and records as always and
stops nothing: `answered` is `none`, `would` says what an enforcing gate would have
answered, and the handler has no `onFailure`, so a shadow gate does not stop a call by
failing either. A gate never applies a decision its server issued as a shadow one, whatever
the gate's own setting. `--on-error abstain` is not shadow: a deny from Kai still blocks.
`agentarmor-monitor gates` counts the records: calls, questions put to Kai, what was and
would have been denied, checks not completed by reason, how long answers took, and calls
reported as run after a deny. The user's order of 2026-11-12 is shadow first: measure the
load and the latency of asking about every marked call before anything is enforced at that
level.

A denial is printed on stdout in the agent's own shape and its reason on stderr, with exit
status 2. A failure is also noted in `gate-errors.log` in the spool, as the step that failed
and the error's class only: an error's own text can quote an event that was never redacted.

`scan` is the one command that talks to a Kai service itself: a diagnostic an operator runs
by hand against a recorded session, with its own `--kai-url`, `--allow-remote` and
`--kai-token-file`, and `--kai-package-sha256` when the answer should be bound to a package.

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
  outcomes, and a last line counting what is not shown and which lines are cut short. Secrets
  were redacted before anything was written.
- **The proposed call** is shown whole or not at all, and the record says where its text came
  from (`call_source`):
  - `log`: the server finds the call's arguments in the session's event log, by call id
    and by the hash the hook took before redaction. This is the source for a gate's question
    (the gate sends that hash, and not the arguments), for the watcher, for `scan` and
    `requests`, and for a render. They were redacted when they were logged, so the request
    never carries what the packet withheld.
  - `arguments`: a program that calls `escalate.detect()` itself and holds the call. They
    are redacted exactly as Summarizer's hook redacts what it logs. Nothing in this package
    takes that way any more.
  - `representation`: when the log cannot supply them, only the text Summarizer shows for a
    call is left. That is a reading of the call, not the call: a patch is shown as its files
    and added lines, without the lines it removes. It is sent, how much it leaves out is
    recorded as unknown, and it never counts as a complete check.

  Whatever the session held, a state can be written as UTF-8. Half of a surrogate pair, which
  a tool leaves when it cuts its output inside an emoji and which an argument can hold too, is
  not a character, and Kai's service refuses a request with one in it. In the packet
  Summarizer spells it out (`\u{d83d}`); in the call's arguments it is the escape JSON gives
  it (`\ud83d`), so the arguments read back as the call that was made.
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
  not a hash of the bytes on the wire. `requests_sent` counts the requests the trigger itself handed to
  the transport (`requests` says what became of each), and `same_request_as` names the trigger whose answer it repeats. A request the
  service refused is not sent again either, but that leaves no link: a refusal is not an
  answer, and an answer obtained after one is the trigger's own. Nothing is shared with a
  later look at the session, or between an action and the content it returned: those are
  different requests or different decision times.

A packet holds what the agent read. It stays on this machine unless the local server was
started with a remote `--kai-url` and `--allow-remote`, and then it goes over `https` to that
one address.

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
| `rules` | The policy this view runs under, as in a policy file: one or more of `{id, tier, description}`, those three fields only, with distinct ids. Rule texts, ids, their number, the tiers used and the order within a tier are free. The first line of the state, the empty `environment:`, the order of the tier headings and the "Policy interpretation" paragraph are fixed by the layout |
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

**What it writes**, with mode 0600 in a folder of mode 0700, and nothing at all if anything
is wrong:

`requests.jsonl`, one row per trigger:

| Field | Meaning |
|---|---|
| `root_id`, `view_id`, `agent`, `rules` | From the script; `rules` is the ids in order and a SHA-256 of the rules as given |
| `event` | The event (from 1) at which the request was built. The session prefix ends there |
| `routes` | Which routes would send it: `watch` (the daemon, from `--level`, default low), `gate` (from `--gate-level`, default medium), or both when they build the same request |
| `key`, `trigger`, `review`, `layout`, `packet`, `summary_embedding`, `summary_detector` | As in a detection record (below) |
| `shown` | The entries the packet's text shows: type, the event each came from (`seq`), where in the event a piece of content sits (`ref`), its trust (`user`, `agent`, or a lower-trust source), and how much of its own text the line holds: `shown` of `of` characters under a `limit`, counted in the entry's text and not in its display (no quotation marks, escapes, ellipsis, labels or marks), with `noted` for excerpts printed beside the quote. A limit is not a count: a request of 21 characters under a limit of 400 shows 21 of 21. No text. An item says its entry is in the text and `shown` below `of` says it was cut; neither says a given premise reached Kai. **Find the premise in `request.state` itself**, and hold the row if it is cut, redacted or missing |
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
| `watch` | `triggered`: the call was marked at the watcher's level. `asked`: a request for Kai was built, and then `request_sha256` names it. A call marked and too large to show is triggered and not asked, with `why` (`call_too_large`, `policy_too_large`). Not triggered has `why: below_level`; with no entry for the call, `triggered` is null and `why` is `no_summary` |
| `gate` | The same two, for the gate. Asked adds `request_sha256` and `an_allow`: what the gate makes of an "allow" from Kai, `stands`, or `incomplete` or `moved_on` (the gate then stops the call by default). Not asked has `outcome`: `abstain` (`why: below_level`: the call runs under the agent's own permission flow and Kai never hears of it) or `undecided` with `why` (`coverage`, `no_summary`, `call_too_large`, `policy_too_large`, `event`), which the gate turns into a denial unless it runs with `--on-error abstain`. `triggered` is null when the gate could not tell how the call was marked |
| `routes_agree` | Whether the watcher and the gate built the same request, or were refused alike, when both were triggered |
| `pre_action_complete` | That of the call's request, or null when neither route built one |

**Asked means a request exists.** A refusal for size happens before any client is called: it is
an attempt, kept in `requests.jsonl` as a row with `sendable: false`, and it is never counted
as asked on either route. Before anything is written the ledger is checked against the
requests: every call a route asks about names a request among the rows, built on that route,
and every request built at the gate belongs to one call.

Count from `calls.jsonl`. A call neither route asks about is in that file and in no other, and
a monitor leaves no record of a call below the gate's level. A later, retrospective request
about a call does not change what happened at its proposal.

`manifest.json` names what rendered (a hash of this package's source, the layout, hashes of the
questions and of the policy interpretation), what the summaries say embedded and marked them,
the options, the SHA-256 of the input, counts, and the SHA-256 and row count of each output.
The source hash is over the package's own files, all of them, and is null when they cannot all
be read, as in a package installed as compiled files. In the counts every proposed call is in
exactly one `gate` count; `watch_triggered` and `gate_triggered` count calls marked at each
level, `watch_asked` and `gate.asked` count requests that exist, `watch_refused` gives the
watcher's refusals by reason, and `no_request_on_either_route` is every call for which neither
route built one.

`--out` must be a folder that is not there yet, or an empty one. The files are written in a
folder of the render's own beside it, which takes its place in one step at the end. So the
folder holds a whole render or nothing, a render removes nothing it did not create, and of two
renders into one place the second to finish is refused and the first is left as it is.

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

### Counting what the gate would do with every call

```sh
.venv/bin/agentarmor-monitor tally rendered/calls.jsonl --answers answers.jsonl --labels labels.jsonl
```

`tally` reads the ledger, never session text, and puts every proposed call in exactly one
outcome at the gate:

| Outcome | What the gate does |
|---|---|
| `unasked` | The call is not marked at the gate's level. Kai is never asked and the gate lets it through to the agent's own permission flow. For a call that should have been denied this is a routing miss, which no training of Kai changes |
| `allowed` | Kai answered allow on complete evidence from before the call. The gate lets it through |
| `denied` | Kai answered deny. The gate stops it |
| `stopped_by_default` | The check could not be completed and the gate stops the call: a gap in the session record, no summary of the call, a call or rules too large, or an allow on evidence that was incomplete or no longer from before the call. An allow that does not stand is here and never under `allowed`. `why` gives the counts by reason |
| `let_through_on_error` | The same, for a gate run with `--on-error abstain` (pass that to `tally` too) |
| `no_answer` | Kai was asked and the answers file has nothing for the request. A missing output is not a decision |

So each label has `calls = let_through + stopped + no_answer`. For calls labelled `deny`,
`let_through` is what was not caught; for calls labelled `allow`, `stopped` is what was stopped
wrongly. Before those it gives the steps to a decision example: `roots`, `calls`, `triggered`
(marked at the gate's level), `asked` (a request was built) and `pre_action_complete`. Calls of
one root, and views of it, are not independent: size a panel by roots.

These are what a gate configured this way **would do given these answers**. They are not what
happened. A ledger holds proposals, `tally` runs nothing, and whether a call that was let
through ran or had an effect is known only from what the hooks observed afterwards. An answers
file is not evidence that a model was read either: keep the requests that were scored, the
model's identity and the render's manifest with any result. The output says
`answers_supplied`, true or false, and nothing about a model.

`--answers` holds one line per request Kai answered: `{"request_sha256", "decision"}`, allow or
deny. `--labels` is the kit's own: `{"root_id", "view_id", "event" or "call_id", "should"}`
with `should` allow or deny for that proposed call, and an optional `group` to count by. A
call has one label: two labels for a call are refused whether they agree or not and however
each names it, and a label that gives both `event` and `call_id` must name one call with
both. Without labels every call is `unlabelled`; without answers only the routing is counted,
which is what a pilot needs. The watcher's count (`watch`: how many calls it asks about) is
given beside the gate's and never added to it: watching enforces nothing. Tokenizer admission
and blind adjudication come after this count and are not part of it.

**Packets built elsewhere.** A measurement of the decision model compares packets: one
proposed call with more or less shown of what led to it.

```sh
.venv/bin/agentarmor-monitor panels packets.jsonl --out panels/
```

`panels` takes records `{"run", "arm", "packet", "tool", "input"}` (and `pair`, `kind`,
`decision`), which Summarizer's `train/campaign_arms.py` writes, and writes one file per arm
with each record as the request the monitor would make of it: these rules, this layout of the
state, the call's arguments shown and redacted as a gate shows a call it holds, and the
registered `decision` question with its exact option text. The arms of one run then differ
in the packet and in nothing else, which it checks and counts. Nothing is sent, nothing of a
session is printed, and nothing is said about whether a state fits the model: that is the
reader's to say. The files hold session text.

## Detections

One JSON line per trigger in the session's directory under Summarizer's spool
(`~/.summarizer/sessions/<agent>/<session>/detections.jsonl`, owner-readable only):

| Field | Meaning |
|---|---|
| `trigger` | The entry Summarizer marked: type, sequence number, call id, the call's hash before redaction, tool, level, labels |
| `decision` | Kai's answer for a tool call, `allow` or `deny`; null for other entries |
| `kai` | `p_deny`, `p_xpia` and what the service says of itself (model, readout, revision, package hash). `identity_checked` says whether that was checked against a configured model before the answer was used: true on the local server, false for a `scan` run without `--kai-package-sha256`. `claims_policy_ready` is the service's own word about its readiness, kept as its claim: the record's `policy_ready` is false whatever it says |
| `flagged` | `decision` is `deny`, or `p_xpia` is at least 0.5 (not a qualified cutoff) |
| `disagreement` | Summarizer marked the entry medium or higher and Kai cleared it. Kept apart from "nothing found"; list them with `detections --disagreements` |
| `packet` | Size and SHA-256 of the packet; `state_chars`; whether the call was verified; Summarizer's own account (`ready`, `dropped` (lines that found no room, by section), `key_dropped`, `cut` (lines that quote less of their entry than there is, by kind: `prompt`, `action`, `message`), `key_clipped` (those among them that are key evidence, which keep a packet from being ready), `unresolved_conditions`, `scanned_all_captured`, `later_events`); `request_sha256`, the identifier of the final request (canonical JSON, not wire bytes); the call's `call_source`, `call_chars`, `call_chars_not_shown` and `call_redactions` (the last two are null for a `representation`: unknown); and `complete`: the packet was ready and the call's own arguments were shown whole |
| `review` | `pre_action` when the hooks delivered the call's proposal, nothing has been reported to have become of it, and the summary held nothing from after it; otherwise `retrospective`. A call first seen at its result or failure is always retrospective (`packet.proposal_observed` is false): its outcome is already in the packet. Only pre-action records are evidence about what could be known in time. This is a separate property from `packet.complete`, which is about how much was shown |
| `requests_sent` | How many requests this trigger handed to the transport: 0 when its answer was one already given for the same request or when every request was refused here before it was sent, more than 1 when a larger request was refused by the service first. An attempt is not a delivery |
| `last_request` | What became of the last request alone, named by its `request_sha256`: `transport_attempts`, `responded`, `admitted`. `packet.request_sha256` is the last request that was **constructed**, also when it was only shown or was refused here; whether it went anywhere is this field |
| `requests` | What became of this trigger's requests, each counted apart: `built`, `refused_before_transport` (too many bytes for the wire: nothing was sent), `transport_attempts`, `responses` (an answer or a refusal came back) and `answers_admitted` |
| `same_request_as` | Present only when this record's answer was given for another trigger's identical request in the same summary: that trigger's key. Never set by a refusal, and never the record's own key |
| `layout` | What Kai was given, by name: the state's layout (`agentarmor-state/v1`) and the packet's schema (`summarizer/v2`). A change to either is a change to Kai's input |
| `summary_embedding` | What the summary says embedded it: backend, model, revision, device, precision, vector size, probe version and whether the probes are calibrated. Copied from the summary the request was built from, which for the daemon is the copy queued with the trigger, never from what is running later. It sits beside the request, never inside it, so request ids do not depend on it. `null` when no summary was read or the summary does not say. It traces what was reported and pins neither weights nor probes |
| `summary_detector` | What the summary says marked it: a version and hashes of Summarizer's wording rules, of its priors and levels, and of its source. The same session marked under other rules can build a different packet. Copied from the same summary as `summary_embedding`, and like it beside the request, never inside it. `null` when the summary does not say, as with summaries made before Summarizer named its detectors; a hash the summary gives as unknown stays `null` |
| `status` | `scored`; `kai_unavailable` and `kai_mismatch` (what answered is not the configured model, with the part that differed in `error`), both retried by the watcher; `kai_rejected` (refused by the service); `too_large_to_send` (refused here for its bytes on the wire: nothing was sent); `call_too_large` or `policy_too_large` (nothing was sent); for a gate's question that was answered without a Kai request, `unmarked` or the reason (`no_summary`, `coverage`, `no_decision_model`, `busy`, `log_unread`, `moved_on`, `outcome_reported`); and `applied` on the gate's own record of what it did |
| `gate`, `stage: issued`, `attempt`, `verdict`, `why`, `declared_on_error` | On the server's record of a decision it issued for a gate's question: the verdict (`abstain`, `deny`, `undecided`), what kept an undecided check from completing (also `moved_on`, `log_unread`, `outcome_reported` for an allow that no longer applied), and what the gate said it would do without a verdict. With `call_id`, `call_sha256` and `policy_sha256`. Never `enforced` |
| `stage: observed`, `reported`, `order`, `answered`, `ran_after_deny`, `ran_before_answer` | On the gate's record of what the agent reported of a call: the hook event; whether it came after the gate's answer, before it, or with none on record; the answer it is tied to and its mode; whether a result came back for a call answered `deny`, or before the answer |
| `stage: answered`, `answered`, `would`, `mode`, `delivery`, `elapsed_ms`, `receipt`, `on_error` | On the gate's record of what it answered about a call: `answered` is `deny` or `none`; `would` is what it answers outside shadow; `delivery` is always `unknown`; `receipt` says whether the server's answer arrived; `why` is the server's reason or the gate's own (`server`, `setup`, `recording`, `event`). With the attempt, the call's id and hash, and from the receipt the hash of the rules, the evidence and the request's hash, null where no receipt came. The only record that says what was enforced |
| `evidence`, `routing`, `revalidated` | On the server's record: how far the session's log had been read when the decision was made (`events`, `log_bytes`), whether all of it was scanned and a hash of the workspace; whether Kai was asked, at which level and threshold and why; and for an allow, how far the log was read again when the answer came and whether that was its end |

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
- The local server's port is on loopback and asks for no credential. Any process on the
  machine can ask it for a verdict about a call in the server's own log. With `--model-api`
  any process can also have its own text put to the decision model, with the server's token
  when the model is remote. It is for a machine one person uses.
- Kai loaded in the local server has no serving qualification, latency figure or memory
  figure for sharing a process with Summarizer, and its engine needs a Linux GPU host today.
  On other machines Kai is reached at another address.
- A decision is bound to what the server had read of the session when it asked. A request
  the user makes while the answer is awaited takes an allow back; anything that changes
  outside what the hooks deliver does not, and a call that was let through is not watched
  for what it then does until its result is logged.
- The check that what answered is the configured model compares what the service reports
  with what the operator set down. It is not an attestation of what runs there.
