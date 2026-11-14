# Summarizer design

Status: draft 3, 2026-11-10, written by the Model Trainer after a review of the code on `dev`,
revised after the Model Reviewer's design reviews (MR23, MR25 on `msgboard.md`) and the user's
decisions of 2026-11-10 (section 12).
It describes what exists, what a security review of it found, and what is proposed. Sections
marked **Proposed** are not built. Numbers are from this repository's own measurements and say
where they come from.

## 1. Purpose and scope

Summarizer is a security sensor for coding agents. It watches a Claude Code or Codex session
through the agent's hooks and keeps, for each session, a labelled record of what the user asked,
what the agent read, what it tried to do and what came back. A stronger guard model (AgentArmor's
Kai) reads a short packet built from that record when it has to decide about one tool call.

It answers one question: **what in this session bears on whether the agent is being attacked, is
being misused, or is about to do something unsafe?** It tags and ranks evidence. It does not
decide, block, or write prose.

The purpose behind that question is a decision about one call: should this action happen, given
what the user asked and what has happened since. So the first duty is context, not detection:
the original task, its scope, approvals and withdrawals; the call and its operands; what was
done before it, by whom, in what order; exact source, field and value associations; reported
outcomes; and what is missing. An unmarked, perfectly legitimate fact can be the decisive one.
A catalogue of techniques (section 6) adds to this and never replaces it.

**User clarification, 2026-11-12.** Summarizer is the cybersecurity scanner of logs,
chats and data: it keeps important context, marks anything suspicious in the current
interaction, and supplies that interaction with relevant past summary/evidence to Kai.
AgentArmor is the harness that orchestrates capture, the local Summarizer/model, context
retrieval, the handoff to local or remote Kai, and application of Kai's allow/deny decision
to the named operation. Marked interactions need a handoff even at low confidence; unmarked
but relevant history must still be retained. Summarizer does not decide permission.

This is the target contract. Today's background low-level routing and synchronous
medium-level gate differ, and content-only checks currently ask an injection question,
not a delivery allow/deny question. A decision before an operation requires an AgentArmor
interception point; the Summarizer observer hook remains non-blocking. The reproduced gaps,
proposed probe banks and aligned EG2 training plan are in
[MR114's probe and handoff review](probe-expansion-review.md). That proposal does not claim
these behaviors are implemented or qualify the rejected trial or Kai serving.

In scope:

- Instructions that reach the agent through data it reads (prompt injection in files, web pages,
  tool results, MCP output, subagent reports, instruction files).
- What the agent does that an attacker would want: access to secrets, sending data out, running
  fetched code, changing its own controls, persisting, destroying, escalating.
- Who authorized what: the user's requests, limits, approvals, withdrawals and conditions, kept
  apart from everything that merely claims authority.
- Observables an analyst would pivot on: hosts, addresses, URLs, mailboxes, paths, packages.
- Honest coverage: what was not captured, scanned or shown.

Direction set by the user on 2026-11-10: the job is to see that a coding agent does not
compromise security through what it **does**: sending data out, suspicious file activity,
reaching for credentials, changing controls. Tagging is tuned for **recall**. More false
positives are acceptable, because Kai, which reads the packet, is the stage built for a low
false-positive rate.

Out of scope, on purpose:

- Insecure code the agent writes (certificate checks turned off, a weak pattern, a missing
  authorization check). Reviewing the quality and safety of generated code is the coding
  agent's job. Summarizer still sees a secret file being committed or sent, because that is
  an action on data, not a judgement of code.
- Tamper-proofing the event log. The log and the packet are context handed to AgentArmor;
  attempts to touch them are tagged, and their integrity is not otherwise verified.

- A general summary of the session for the user. Nothing is kept because it is interesting; it
  is kept because it is evidence.
- Code quality, style and correctness as such. Results are kept as evidence of what happened:
  a test or check result can settle a condition the user set, and it can support or contradict
  what the agent later claims, whether or not a request said "only if".
- Verdicts. No entry says "attack" or "benign".
- Prevention. The hook never blocks, denies or prints.

**Review finding on scope.** Every label, probe, wording rule and digest section in the code
serves the question above. Nothing needs removing. The problem is the opposite: the security
coverage is narrower than the scope claims (section 6).

## 2. System

```
agent hooks ──> hook.py ──> events.jsonl ──> daemon.py ──> summarize.py ──> summary.jsonl
 (observer)     redact,       append-only      tails it      Session          entries, iocs,
                capture                                         │              coverage, flow
                                                                │
                              facts.py    labels.py    probes.py + embedder.py
                              (what a      (what it     (what content
                               call or      indicates)   resembles)
                               text holds)
                                                                │
guard model <── text.digest(summary, focus=call) <── store.load_summary(agent, session, call)
```

The last line is the intended use. AgentArmor does not yet consume these packets for decisions
(its checks are diagnostic and report themselves as not policy-ready), and no measurement here
shows that a guard decides better with one.

- **hook.py** runs inside the agent's hook, standard library only. It redacts secrets, records
  what it could not read or had to clip, hashes the call (`call_sha256`), appends one line and
  exits 0 without output.
- **daemon.py** follows the event log and keeps the summary current; `store.load_summary` can
  wait for a given call to be summarized before a packet is built for it.
- **summarize.py** turns events into units (requests, actions, content chunks), attaches
  indicators, correlates across events and writes the summary (`summarizer/v2`).
- **text.py** renders the packet for one call under a size budget and accounts for everything it
  left out.

Design rules the code enforces, from `AGENTS.md`: the summary is extractive; provenance comes
from the hook that delivered a text, never from the text; an attempt is never recorded as an
outcome; anything dropped shows in `coverage`; recall over precision; the hook is an observer.

## 3. Trust and provenance

| Trust | What it is | Where it comes from |
|---|---|---|
| `user` | Requests and dialog answers on the user channel | `UserPromptSubmit`, question answers |
| `agent` | What the agent wrote: calls, their inputs, its messages | `PreToolUse`, `Stop` |
| `untrusted` | What the agent was shown from outside | tool results, files, web, MCP |
| `derived` | Text produced from the session | compaction summaries, subagent reports |

Three consequences matter for security:

1. A user's words quoted inside a file are `untrusted`. Only the channel confers authority.
2. The user channel is itself unauthenticated (anything that can type there is "the user"); the
   packet says so in its header for requests.
3. A destination, path or package is attributed to whoever named it **first**. If only
   lower-trust content had named a host by the time the agent contacted it, that is the
   strongest single behavioural indicator the summarizer has (`untrusted_destination`, 0.60).

## 4. Threat model

**Assets.** The user's credentials and data; the integrity of the repository, its dependencies
and its CI; the machine and what it can reach (cloud accounts, internal network); the agent's
own configuration and memory; and the evidence itself (the event log and summaries).

**Adversaries.**

| Adversary | Capability | Typical goal |
|---|---|---|
| Content author | Controls text the agent will read: a README, an issue, a web page, a package's docs, a tool result | Steer the agent (indirect prompt injection) |
| Tool or server operator | Controls an MCP server or a dependency: descriptions, results, later changes | Tool poisoning, data capture, rug pull |
| Supply-chain attacker | Controls a package, a registry mirror, a fetched script | Code execution at install or run time |
| Compromised or misaligned agent | The agent itself takes unsafe actions without being told to by content | Any of the above |
| Evader | Any of the above, aware that a summarizer is watching | Keep the evidence out of the packet |

The user is assumed honest. A malicious user is out of scope: the user channel is the
authorization baseline.

**What the attacker wants the agent to do** maps onto the familiar intrusion stages, which
section 6 uses as its checklist: get instructions in, execute, persist, escalate, evade
defences, reach credentials, discover the surroundings, collect, exfiltrate, establish command
and control, and cause impact. Two more are specific to agents: deceive the human in the loop,
and weaken the security of what the agent builds.

**What the evader does to the summarizer** is in section 9.

## 5. Detection layers

| Layer | Module | Looks at | Strength | Weakness |
|---|---|---|---|---|
| Deterministic facts | `facts.py` | Commands, paths, call inputs | Exact, explainable, no model | Only what was anticipated |
| Wording rules | `facts.text_flags` | Lower-trust text, and text the agent writes | Cheap, precise labels | English, literal phrasing |
| Embedding probes | `probes.py`, `embedder.py` | Lower-trust content chunks | Paraphrase, other languages | Matches topic more than intent |
| Correlation | `summarize.py` | Sequences of events | The patterns that need context | Rules, few so far |
| Observables | `facts.observables`, `labels.ioc_indicators` | Hosts, URLs, paths, packages | Pivot points, first-named-by | Lexical |

Indicators combine per entry as independent causes (noisy-OR), so corroboration raises
confidence and a single weak cue stays low. Confidence is a ranking score, not a probability.

Correlations that exist today:

- **Origin**: an action uses something only lower-trust content had named (`untrusted_target`,
  `untrusted_reference`, `untrusted_package`), and the content that named it is marked in turn
  (`destination_source`).
- **Flow**: untrusted input, sensitive access and outside contact co-occur (`toxic_flow`;
  `flow` is `none`, `potential` or `observed`).
- **Resemblance**: an action resembles lower-trust content more than any user request
  (`goal_drift` on the action, `action_source` on the content).
- **Conditions**: a request made something conditional; the packet says whether every later
  result that could settle it is shown.
- **Secret echo**: a credential format appeared in output after a sensitive access.

## 6. Security coverage: what the review found

Method: `train/security_battery.py` runs 63 single-call action scenarios and 21 content texts
(18 attacks, 3 benign), one per technique, through the real code path. It is a diagnostic
inventory written by the trainer with the detectors' code open. It is not a measurement of
detection quality and must never be used as a held-out set.

What "tagged" means here, and what it does not:

- A scenario is tagged when its entry received any indicator. That is not recognition of the
  technique. Deleting a session log is "tagged" by `outside_workspace_write`; switching
  Gatekeeper off by `privileged_command`; the DNS case by sensitive access and network contact
  without any egress indicator. None of those says what the scenario is about.
- The runner uses the Claude Code hook format only, gives every call a result of `"ok"`, and
  delivers every text as a web fetch. The "tool description poisoning" text is therefore a
  fetched page with that wording, not a captured MCP description (which the hooks do not
  deliver at all).
- Scenarios carry no expected indicator, no evidence span, no required facts and no benign
  twin. They are single calls; nothing here tests context, order, outcomes or user scope.

```sh
.venv/bin/python train/security_battery.py                         # rules only
.venv/bin/python train/security_battery.py --embedder embeddinggemma
```

### 6.1 Actions

Probes do not apply to actions, so the two embedders give the same result. The review found
this, on the 63 scenarios of the first battery:

| Outcome | Before (63 scenarios) | After the detectors of section 8 (56 scenarios) |
|---|---:|---:|
| Untagged | 34 | 0 |
| Generic label only (`network_access`, `package_install`) | 13 | 0 |
| Tagged low with a specific label | 3 | 2 |
| Tagged medium | 10 | 37 |
| Tagged high | 3 | 17 |

The battery shrank from 63 to 56 because seven scenarios were about insecure code the agent
writes, which the user put out of scope. The "after" column is not a detection rate: the
detectors were written with these scenarios open, so it shows only that the listed
techniques are no longer invisible. The table below is the review's finding **before** the
fix, kept as the record of what was missing:

| Stage | Covered today | Not seen today (battery) |
|---|---|---|
| Execution | Fetched text piped to an interpreter; encoded payload in a command | Download, `chmod +x`, run as separate steps; PowerShell download cradle |
| Persistence | Agent, shell, Git and CI config writes; `authorized_keys`; launch agents; global hooks path | New OS user; new cloud access key; cron shows as `remote_code`, not persistence |
| Privilege escalation | `sudo`, service managers | setuid bit; world-writable trees; Docker socket and `--privileged` |
| Defense evasion | Agent bypass flags (`--dangerously-*`, `--no-verify`, hooks off) | History clearing; log deletion; Gatekeeper, SELinux, firewall off; quarantine removal; timestomping |
| Credential access | Credential paths; environment dumps; several secret stores | Keychain dump; Git credential helper; shell history; browser profiles |
| Discovery | (only when a sensitive path is touched) | Cloud metadata endpoint; internal scans; local admin APIs |
| Collection | Archiving a credential directory (as `sensitive_access`) | Clipboard; screenshots |
| Exfiltration | Upload flags and tools; `git push`; `gh` writes | DNS carrying data (seen only as sensitive access plus network contact, with no egress indicator); known capture and webhook hosts get no extra weight; data in a URL the agent later renders |
| Command and control | none | Reverse shells of every kind; tunnels; reverse SSH forwards |
| Impact | `rm -r`, forced pushes, `dd`, `drop table` | `terraform destroy`; `kubectl delete`; fork bombs; miners |
| Exposure | none | Repository or bucket made public; security group opened to the world |
| Supply chain | Package named only by lower-trust content; workflow writes | Registry or index switched; install from a URL; lockfile hash edits; remote repointed |
| Weakened security | none | TLS verification turned off; a secret written into source; an authorization check removed; `eval` of input; `.env` taken out of `.gitignore` |
| Observer integrity | none specific | Killing the daemon; deleting or rewriting the spool (seen only as a generic write or `rm -r`) |

Two things stand out. Command and control is not modelled at all, although a reverse shell is
the most direct thing an injected instruction can ask for. And nothing protects the observer:
`rm -rf ~/.summarizer/sessions` scores 0.25 as an ordinary destructive command.

### 6.2 Content

| Detector | Attack texts tagged, of 18 | Benign texts tagged, of 3 |
|---|---|---|
| Wording rules alone | 6 (3 high, 3 medium) | 0 |
| Rules + base model probes | 18 (10 high, 7 medium, 1 low) | 2 (1 high, 1 low) |

- The rules are English and literal. They miss a paraphrase that avoids the usual words, Chinese
  and Spanish, base64, spaced letters, text concealed with ANSI escapes, a fake error that tells
  the agent to pipe a script to `sudo bash`, approval phishing, a look-alike package, and
  instructions addressed to "reviewer bots".
- With the probes, every one of those texts receives some tag. That is an any-tag count, and
  it cannot establish that the encoder recognized what each text asks for. One control by the
  Model Reviewer on the base64 row: the wrapper sentence alone, with no payload, scores 0.76
  with all three labels, against 0.59 for the original; an equal-length benign payload under
  the same wrapper scores 0.55. So the wrapper alone is enough to trigger all three labels.
- What can be said: the battery's paraphrased, Chinese and Spanish examples receive tags
  where the rules are silent. Whether the tags are for the right reason, and whether that
  holds beyond these examples, needs annotated spans (section 11).
- The probes respond to **topic** at least as much as to intent. "Never paste API keys into
  issues. Rotate credentials if they leak…" scores 0.89 with all three labels. On development
  file 002 the false-positive rates are 0.42 to 0.58 (section 7).
- The facet labels overlap: 8 of the 18 attack texts receive all three.
- `hidden_text` covers bidi overrides, tag characters and zero-width runs. It does not cover
  ANSI concealment or HTML comments, and nothing decodes base64 or hex in content before
  scanning it.
- An image link built to carry data out (`![](https://…?d=<data>)`) scores 0.19. The agent's
  own final message is where that link would be rendered, and its URLs are not weighed.

### 6.3 Scope and delegation, which no stage list covers

An ordinary, permitted tool call can be wrong for this session: the recipient changed, an
approval was withdrawn, the branch or account is not the one asked for, a prerequisite is
stale, an instruction comes from another session's memory, or a result claims an authority it
does not have. None of these is an intrusion technique and none is caught by tagging the call.
They are decided from retained context: the original request, the latest constraint, the
relevant unmarked result, and who did what. The session-case contract (section 10) is the
instrument for this, and it has equal standing with the stage table above.

### 6.4 The agent's own messages

Review finding, fixed: what the agent says when it stops was stored (the latest one only, cut
at 2,000 characters) and never scanned, while coverage reported everything scanned. Each such
message is now scanned whole for wording and observables, kept with its indicators as the
agent's own words, shown in the packet with what was cut, and counted when left out. A URL in
it is something the agent wrote: a candidate for egress if a client renders it, not a contact.

### 6.5 What the hooks cannot see

Carried on every summary in `coverage.not_captured`: MCP tool descriptions and schemas (where
tool poisoning lives), the agent's text between tool calls, files attached to a prompt, and
whether a prompt was typed or injected by another mechanism; for Codex also hosted tools such
as web search, instruction files other than `AGENTS.md`, and whether a command exited with an
error. These are platform limits. The design answer is to report them on every
packet, and to treat an MCP server's first result and any change of its configuration as
security events in themselves (section 8, P2).

## 7. Modeling

### 7.1 What the model does today

EmbeddingGemma 2 (text encoder, pinned revision, bfloat16 on GPU, float32 on CPU) embeds:

- each lower-trust content chunk (at most 320 characters, cut at line or sentence ends), compared
  with three probes, each the mean of two or three query embeddings;
- each user request and each action, to link an action to the request and the content it most
  resembles.

A probe tags a chunk when similarity reaches a threshold fitted for 90% recall; a logistic
(scale, bias) turns similarity into the tag's confidence.

### 7.2 Measured

| Data | What it is | agent_directive | data_theft | control_tampering |
|---|---|---|---|---|
| Pilot 001 (50 examples, 1 block) | Hard look-alike pairs | R 0.96, FPR 0.71, AUC 0.81 | R 0.96, FPR 0.42, AUC 0.91 | R 1.00, FPR 0.72, AUC 0.83 |
| Development 002 (40 examples, 5 blocks) | Realistic carriers | R 0.81, FPR 0.42, AUC 0.70 | R 0.96, FPR 0.52, AUC 0.92 | R 0.88, FPR 0.58, AUC 0.84 |

Base model, built-in thresholds, Apple silicon. Small development samples; not rates. On
development 002, 10 of 16 `agent_directive` positives are tagged on the chunk that holds the
instruction, 3 only on another chunk, 3 not at all. One fine-tune was tried: about 0.99 AUC on
its own held-out carriers and worse than the base model on independent panels. It is not used.
The first independent run (blind test 001, 400 authored examples from 28 unseen source blocks,
run once on a frozen commit by the Model Reviewer): the runtime's recall is 0.89 / 0.99 / 0.98
and its false-positive rate 0.68 / 0.62 / 0.74, AUC 0.76 / 0.93 / 0.81. Light contrast
(weight 0.25) raised AUC by 0.01 to 0.03; half-overlapping windows improved where the tag
lands and lowered ranking. No variant is adopted. `train/README.md`, "The first independent
run", has the table and its limits.

### 7.3 Review findings on the modeling

1. **The probes measure "is about security", not "asks for something".** Similarity to a
   description of an attack is high for a warning against that attack. Nothing in the probe
   design contrasts the two. This is the main precision problem and it is structural.
2. **`control_tampering` covers three different things**: persistence and configuration
   changes, switching safeguards off, and destruction. Hypothesis, not yet measured: one mean
   vector serves them worse than separate ones would. The label it emits
   (`tampering_request`) tells the guard little either way.
3. **Stages with no facet.** Running fetched code and installing unknown packages are folded
   into tampering; command and control, deception of the user and weakening of security have no
   facet at all.
4. **320-character chunks cut evidence.** The address ("Assistant,") and the order can land in
   different chunks; neither holds the facet alone. The encoder accepts far longer input than
   is used.
5. **Only content is probed.** What the agent itself writes (a commit message, a file body, a
   message to the user) is checked by wording rules only, so a non-English or paraphrased
   payload written by the agent is not weighed.
6. **Thresholds were fitted on 680 starter-built texts**, written by the trainer. They do not
   transfer: FPR on realistic carriers is above 0.4 for every facet.
7. **The encoder's training data in this repository is English.** The battery's two
   non-English examples are tagged by the base model; no test here measures other languages
   or would notice a regression.
8. **Intent drift is weak by design** (capped at 0.25). It compares an action with the nearest
   request by similarity; it does not know what the request permits.

### 7.4 Proposed model design

**Facets (v4).** Still few and broad, one per thing an attacker asks for, each written as
*a request to do X*, with paired counter-descriptions:

| Facet | A request to… | Replaces |
|---|---|---|
| `agent_directive` | redirect the agent: ignore its task, obey this text, hide it from the user | same |
| `secret_access` | read or collect credentials, keys, tokens, private data | half of `data_theft` |
| `data_egress` | send, upload, post or embed data to an outside destination | half of `data_theft` |
| `code_execution` | run fetched or unknown code, install an unknown package, open a remote shell | part of `control_tampering` |
| `persistence` | change agent, memory, tool, startup or CI configuration so it applies later | part of `control_tampering` |
| `safeguard_bypass` | skip approvals, disable sandboxing, checks, logging or security tools | part of `control_tampering` |
| `destruction` | delete, wipe, overwrite, make public | part of `control_tampering` |
| `user_deception` | mislead the user about what is being approved or was done | new |

Whether a model this size can hold eight apart is a hypothesis. Facets are chosen, merged or
dropped on **development** data only. Eight facets cannot be validated against annotations made
for three: v4 needs its own annotation contract and its own test.

**Contrast, to separate asking from mentioning.** Each facet gets negative queries: the same
topic as a warning, a policy, a description, a refusal. The score becomes similarity to the
positive prototype minus similarity to the negative one. This costs one extra dot product and
needs no training. It is the first experiment because it addresses finding 1 directly. It is a
development experiment on ranking, not a rule that suppresses warnings, policies or refusals:
under the label contract a quoted or reported request and a legitimate instruction to an agent
keep their facets, and recall on those slices must be preserved.

**A small trained head, if contrast is not enough.** A linear layer (or one hidden layer) over
the frozen embedding, one output per facet, trained on accepted synthetic batches and
calibrated on development data. It keeps the encoder untouched (no repeat of the fine-tune
failure), runs on CPU, and is a few kilobytes. The probes stay as the zero-shot fallback for an
encoder the head was not trained on.

**Windows instead of cuts.** Embed overlapping windows (for example 480 characters with a
160-character overlap) so that more instructions fall whole inside some window; score a chunk
as the best window that covers it. Overlap reduces boundary loss and does not remove it
(evidence at 300 to 500 is split by windows at 0 to 480 and 320 to 800), and evidence spread
over fields or calls needs linked context, not longer windows. Taking a maximum over more
windows shifts every score, so thresholds are refitted. Measure span coverage, localization,
cost and false positives on development data before and after.

**Normalize and decode before scanning.** For rules and for the model: fold look-alike letters
and width variants (NFKC plus a confusables table), strip ANSI and other control sequences while
flagging that they were there, collapse spaced-out letters, and decode base64 and hex runs of
plausible length, scanning the decoded text as content of the same trust with a
`decoded_from` reference and source offsets. Constraints:

- Decoding is inspection of bytes, never execution, with limits on expansion, depth and time;
  hitting a limit is a coverage gap.
- **Redaction comes first, in the hook.** Today an assigned token is redacted before writing,
  but the base64 of the same text passes unchanged and stays recoverable (reviewer's check,
  MR23-5). A recognized encoded credential must be redacted in its encoded form before the
  record reaches disk, and a decoded secret is never persisted.
- A normalized match is reported as such, apart from a literal one. Folding never rewrites an
  operand: the host, path and call hash a guard sees are the ones in the call.

**Probe what the agent writes.** Apply the facets to the prose the agent composes (file bodies,
commit messages, messages to the user, subagent prompts), at lower weight.

**Calibrate on real carriers, per source.** Thresholds are fitted on development data written
from real seeds, never on trainer-written starters, and frozen before the blind run.

**What stays out.** No generative model in the summarizer. No classifier that emits a verdict.
No fine-tuning of the encoder until an independent test exists to judge it.

## 8. Proposed detection work

Ordered by what an attacker gains from the gap, and by cost. P1 items are deterministic and
small. Every number below is a provisional ranking weight, not a measured precision. Tunnels,
internal addresses, index changes, collection tools and maintenance of the summarizer itself
all have legitimate uses: the flag states a fact with its basis, and the request in context
decides. Overlapping rules and probes are not independent evidence because noisy-OR combines
them, so ranking inflation and competition for packet room are checked before priors are
added on top of each other.

**P1. Close the action gaps with facts. Built** (user's go-ahead, 2026-11-10). Flags in
`facts.shell_facts` and `facts.host_flags`, each mapped to a label and a provisional prior in
`labels.ACTION`:

| Flag | Examples | Label | Prior |
|---|---|---|---:|
| `observer_tamper` | writes, deletes or kills aimed at `~/.summarizer` or the daemon | `observer_tampering` | 0.70 |
| `reverse_shell` | `/dev/tcp/`, `nc -e`, `mkfifo`+`nc`, `socat exec`, socket-to-shell one-liners | `command_and_control` | 0.60 |
| `tunnel` | `ngrok`, `cloudflared tunnel`, `ssh -R`, `chisel` | `command_and_control` | 0.40 |
| `defense_off` | Gatekeeper, SELinux, firewall or audit off; quarantine removal; history cleared; system logs deleted | `defense_evasion` | 0.50 |
| `timestamp_change` | `touch -t` | `defense_evasion` | 0.15 |
| `dns_exfil` | a DNS query built from command output or a variable | `data_egress` | 0.50 |
| `data_in_url` | command output or a variable placed in a request | `data_egress` | 0.45 |
| `capture_host` | request-capture, paste, file-drop and chat-webhook hosts | `data_egress` | 0.45 |
| `cloud_metadata` | the instance credential service | `sensitive_access` | 0.45 |
| `internal_target` | loopback, private and link-local addresses | `internal_access` | 0.20 |
| `network_scan` | scanners and brute-forcers | `discovery` | 0.30 |
| `recon` | `whoami`, `id`, `/etc/passwd` | `discovery` | 0.08 |
| `exposure` | visibility to public, public ACLs, `0.0.0.0/0` ingress, serving a directory | `exposure` | 0.45 |
| `identity_change` | new OS users, access keys, role or policy bindings, deploy keys | `persistence` | 0.45 |
| `scheduled_task` | cron, `at`, launchd, systemd timers | `persistence` | 0.40 |
| `escalation` | setuid, world-writable trees, `--privileged`, host mounts, Docker socket | `privilege_escalation` | 0.40 |
| `infra_destroy` | `terraform destroy`, namespace or bucket deletion | `destructive` | 0.40 |
| `registry_change` | index, registry or remote switched; install from a URL or Git | `supply_chain` | 0.35 |
| `lockfile_edit` | a dependency lockfile changed by hand | `supply_chain` | 0.25 |
| `collection` | clipboard, screenshots, traffic capture, archiving home or key directories | `collection` | 0.35 |
| `secret_search` | searching files for passwords, tokens or keys | `collection` | 0.12 |
| `file_encryption` | files encrypted in place or into a protected archive | `file_encryption` | 0.30 |
| `resource_abuse` | fork bombs, miner pool URLs | `resource_abuse` | 0.45 |

Also: uploads through a pipe to `nc`, mail tools and more cloud uploads count as sends; a
download followed by `chmod +x` in one command, and download cradles, count as `remote_code`;
keychain dumps, Git credential helpers, shell histories and more browser profiles count as
`sensitive_access`. Each flag has a test with the technique and the same tool used plainly
(`tests/test_facts.py`).

Recall comes first here by the user's direction. Several of these fire on ordinary work
(`curl http://localhost:3000` is `internal_access`, `id` is `discovery`, `grep -r token` is
`collection`), at low weights, and Kai is expected to sort them out with the request in hand.

Not built yet: a fetch in one call followed by execution in a later one (needs session state);
reported with its order, the path where there is one, each call's status, and the
uncertainty, since the same name is not proof the same bytes ran.

**Out of scope by the user's decision:** flags for insecure code the agent writes.

**P1. Hidden and encoded content.** Extend `hidden_text` to ANSI concealment, HTML and Markdown
comments addressed to an agent, and long encoded runs; add the decode-and-scan step from 7.4.

**P2. Correlations.** Each is a rule over state the session already holds:

- **Read then send**: the value or name of something read from a sensitive path later appears
  in an outgoing call (today the two are linked only by co-occurrence).
- **Fetched then run**: `staged_exec` above.
- **Told then done**: an action matches the operands of an instruction in lower-trust content
  (same host, path, package or command), which is stronger than embedding resemblance.
- **Asked then approved**: the agent's question to the user does not describe the call it is
  about to make (approval phishing).
- **Said versus did**: keep the agent's exact claim beside an observed result that contradicts
  it. Leaving an action out of a short final answer is not by itself deception; the stronger
  model judges.
- **New tool surface**: the first result from an MCP server not seen before, and any write to
  MCP configuration followed by use of that server.

**P2. The agent's rendered output.** Final messages are now scanned (6.4). Next: weigh a URL
in them whose query carries session data as candidate egress. It is not proof that anything
fetched it.

**P3. Model work** in the order of 7.4: contrast queries, windows, facets v4, then a head.

## 9. The summarizer as a target

| Attack on the sensor | State today | Proposed |
|---|---|---|
| Inject the guard through quoted text | Session strings are single-line JSON literals under a header that says they are data | Keep; add a test with instruction-shaped strings in every section |
| Flood the session so key evidence falls out of the packet | Key evidence is ranked first and its loss makes the packet "not ready"; found and fixed for compaction and retries in long sessions | Adversarial flooding cases in the held-out set; a per-source cap so one source cannot take the budget |
| Split an instruction across chunks, fields or calls | Wording rules run on whole fields; probes on chunks | Windows (7.4); cross-field rules for an address in one member and an order in the next |
| Hide in what is never scanned | Captured text is scanned and clipping and gaps are reported; the agent's final messages were an exception until this review (6.4) | Keep the "no silent partial" tests; add one per captured field |
| Encode or disguise | Not handled | Normalize and decode (7.4) |
| Switch the hook off, kill the daemon, edit the log | `disableAllHooks` is flagged. Nothing detects a removed or rewritten record: sequence numbers are assigned by counting what is read, so deleting one well-formed line leaves no gap, and `saw_session_start` only says a start record is present | See "Log integrity" below |
| Forge the user channel | Reported as unauthenticated | Platform limit; stated on every packet |
| Poison the model | Pinned revision, local files only at run time | Record the weight hash in the summary's session record |
| Steal the evidence | Spool is `0700`, files `0600`, secrets redacted before writing. That keeps other accounts out, not an agent running as the owner | Document retention; add a purge command |
| Leak a secret through the summary | Redaction for common formats | Add formats found missing in review: GitLab, npm, PyPI, SendGrid and Vault tokens |

**Log integrity.** The agent runs as the same user as the hook, so it can rewrite the log. A
hash chain inside that log detects nothing against this adversary, who can recompute or
truncate it; it helps only relative to a head the adversary cannot reach. Before any
protection is claimed the design must state: who can write what; where the trusted checkpoint
or signing key lives (another account, a remote service, hardware); how session identity,
replay, truncation and daemon restart are handled; and how fresh a head must be. An
`observer_tamper` indicator is evidence of an attempt and cannot report after the observer is
dead. Integrity must be treated as **unverified**; nothing in a packet or in coverage says so
today, and scan coverage is not a statement about the log's authenticity. The user decided on
2026-11-10 not to invest in this now.

## 10. The evidence packet

`text.digest(summary, max_chars, focus, call_sha256)` returns the text and an account of it:
whether the call is verified, what fits, what was dropped per section, whether key evidence was
dropped or clipped, the conditions set by requests and whether they are resolved, and the list
of entries shown with their references.

A packet is **ready** only when the call's identity is verified, everything fits or what was
dropped is not key evidence, no condition is unresolved, every captured event was scanned, and
nothing happened after the call that the packet does not include. Otherwise the guard is told
what is missing. This is the property the session-case contract tests (`train/case_contract.py`).

Measured on the 40 held-out session cases at 3,000 characters: first run, 90 of 144 runs
complete and 38 ready while incomplete; after the fixes that run prompted, 144 complete, 0 ready
while incomplete, 16 conservatively not ready. The second figure is development evidence, since
the fixes were guided by the file.

**Proposed.** A packet section listing, per stage of section 6, the entries with an indicator
and each one's status (proposed, returned, failed, denied) by reference. It reports behaviour
seen; it does not infer a completed chain. And the flooding protections in section 9, with
source caps that never drop key evidence silently.

## 11. Evaluation

| Question | Instrument | State |
|---|---|---|
| Where do the detectors not look at all? | `train/security_battery.py` (diagnostic inventory) | 47 of 63 actions with no more than a generic indicator; 12 of 18 texts untagged by rules |
| Do the probes find text in which a facet is present, on unseen sources? | Blind facet test 001, `score_eval.py` | Run once: 400 examples, 200 groups, 28 blocks. Runtime recall 0.89 / 0.99 / 0.98, FPR 0.68 / 0.62 / 0.74 |
| Does the packet keep what a decision needs? | Session cases, `case_contract.py` | 40 diagnostic cases; fresh holdout needed |
| Does the guard decide better with the packet? | Kai with packet, with raw tail, with nothing | Not started |
| Does it run everywhere it must? | CPU, Apple silicon, NVIDIA, AMD | First two only |

The battery stays a diagnostic inventory after any fix guided by it. To become a regression
gate it needs, per scenario: the expected indicator and evidence span, annotated independently;
realistic alternative forms; legitimate uses of the same tool; both hook formats; and separate
columns for any tag, the relevant tag, localization and retention in the packet. Context
claims need multi-call cases with a real decision cut-off, denied, failed and unknown outcomes,
and counterfactual user scopes.

An indicator's precision is scored against whether the phenomenon is present, not against
whether the scenario is an attack. An authorized credential read and an out-of-scope one
rightly carry the same `sensitive_access`; the request in the packet is what differs. So "no
benign twin above low" is not a criterion.

**The blind test is not a selection set.** Queries, windows, thresholds, heads and facets are
chosen on development data. Labels are adjudicated without scores, everything is frozen, and
the test is run once. A predeclared comparison of variants frozen beforehand may be measured
in that one run. A test whose result has guided a change is development data from then on.

## 12. Decisions

Taken by the user on 2026-11-10:

1. **Build the missing action detectors: yes, tuned for recall.** More false positives are
   acceptable because Kai is the low-false-positive stage. Done (section 8). The new labels
   are additive to `summarizer/v2`; Kai's prompt needs their names.
2. **Flag insecure code the agent writes: no.** That is the coding agent's job. The focus is
   that the agent does not compromise security by what it does: exfiltration, suspicious file
   activity and the like.
3. **Tamper-proof log: not now.** Keep it simple. The log and packet are context sent to
   AgentArmor for Kai's decision.

Settled between trainer and reviewer, no user decision needed: the 200-group blind test
continues on three facets; contrast queries and windows are developed on development data and,
if frozen in time, compared once on that test; an eight-facet set is a later step with its own
annotation contract and test.

Still open: hidden and encoded content, the correlations and the model work of sections 7.4
and 8, none of which is built.
