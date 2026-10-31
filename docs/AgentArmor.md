# AgentArmor architecture and refactoring guide

> Design snapshot: 2026-10-30. This document describes both the current
> implementation and the intended architecture. Statements marked **Current**
> describe behavior in this repository today. Statements marked **Target** guide
> future refactoring and are not promises about the released product.

AgentArmor is a policy-enforcement layer for coding agents. Codex and Claude Code
provide lifecycle hooks, users provide the policy and authorized task, and Kai
provides the semantic security decision. The target distribution is a native
`agentarmor` executable that handles hooks, CLI checks, and server operation
without requiring Python on the user's machine. It preserves trust and
provenance and translates a denial into the native hook protocol before a
covered effect occurs.

This document turns the findings in
[Recent attacks on Codex and Claude agents](agent_attacks.md) into a product and
software architecture. The companion
[Kai requirements](https://github.com/min-mwei/Kai/blob/dev/docs/Kai_requirements.md)
defines the trainer-owned model contract in the Kai repository.

## Product contract

The intended contract is:

```text
user or administrator
  authors policy + grants a task
          |
          v
Codex / Claude lifecycle hook
  captures a typed event before or after an agent action
          |
          v
AgentArmor
  validates complete input
  preserves source and authority
  adds deterministic effect facts
  retrieves relevant session state
          |
          v
Kai
  evaluates the event against policy and task
  returns calibrated probabilities + evidence offsets
          |
          v
AgentArmor adapter
  deny  -> explicit native hook denial
  allow -> abstain; normal agent permissions still apply
  error -> explicit denial for synchronous checks
```

AgentArmor is not a replacement for Codex or Claude permissions, an operating
system sandbox, an endpoint security product, or a general-purpose malware
scanner. It is a separate policy decision point. It can stop a covered action
only when the agent calls a synchronous hook before the action.

## Target distribution: one AgentArmor executable

**Current migration:** `native/kai` now contains the adopted engine, native
command entry point, encoded diagnostic transport, and security contracts.
The Python `armor/` service/package, agent launchers, `.env.example`,
`install.py`, and `pyproject.toml` have been removed. Python remains only in
optional developer tests; training/export/MLX reference tools belong in Kai.
The native text policy path remains unqualified; `doctor` reports
`policy_ready: false` and native synchronous hooks deny explicitly. The full
commands and policy APIs below remain target behavior unless noted.

The initial supported platform is macOS on Apple Silicon M4+. Preserve an M4
baseline while optimizing the M5 Max Metal path through capability-based
dispatch. The [native security guide](native_security.md) defines sanitizer,
threading, malformed-input, GPU buffer, memory-lifetime, and release gates.

```text
Codex / Claude
      |
      | native lifecycle JSON on stdin
      v
agentarmor hook --agent codex_code|claude_code
      |
      +--> local IPC --> agentarmor serve
      |                   |
      |                   +--> policy/event/state pipeline
      |                   +--> native tokenizer and encoder
      |                   +--> linked Kai CPU/Metal engine
      |                   +--> calibrated decision and evidence mapping
      |
      +--> in-process evaluation for an explicit one-shot CLI invocation
      |
      v
native deny JSON, abstention, or advisory output
```

There is one installed executable and one public product boundary. A resident
server may be a second process of that executable so weights stay loaded across
hook invocations. One-shot CLI operation uses the same evaluation library and
loads its model locally; its cold-start cost must be measured separately.

Target commands; package/socket/check/scan/run support is not implemented yet:

```sh
agentarmor serve --device gpu --package /trusted/kai-package --socket /trusted/armor.sock
agentarmor serve --device cpu --package /trusted/kai-package --socket /trusted/armor.sock
agentarmor hook --agent codex_code --socket /trusted/armor.sock
agentarmor hook --agent claude_code --socket /trusted/armor.sock
agentarmor check --device auto --package /trusted/kai-package --policy /trusted/policy.jsonl --task-file /trusted/task.txt
agentarmor scan /work/document.txt --device cpu --wait
agentarmor run --agent codex_code -- codex
agentarmor doctor
```

`check` consumes a structured event from stdin. `hook` adapts native lifecycle
events. `run` is the trusted prelaunch path for bootstrap checks. The CLI and
server share policy loading, normalization, state, rendering, tokenization,
readout, and failure semantics.

Installed hook commands should reference the executable by an absolute path:
`"/trusted/bin/agentarmor" hook --agent claude_code`. They must not invoke
`python3`, a Python virtual environment, a hidden Python subprocess, or a
separate `kaiinfer-server`.

Local IPC should authenticate the OS user through a Unix socket or Windows named
pipe with restrictive permissions. Loopback HTTP is an optional compatibility
transport; remote server use requires authenticated TLS. If server mode is
selected and the server is unavailable, the synchronous hook emits denial.
It must not silently switch to an unqualified backend.

One-shot mode cannot acknowledge a durable queued scan and then exit with the
only copy of that job. It must finish the scan, hand it to an existing resident
service, or return an explicit unsupported/incomplete result.

### CPU and GPU selection

Expose `--device cpu|gpu|auto` on model-executing CLI commands and server startup.
The recommended default is `auto`:

| Option | Behavior |
| --- | --- |
| `cpu` | Use a qualified CPU engine and allocate no GPU model resources |
| `gpu` | Use a qualified GPU engine; fail clearly if the device, build, package, or qualified backend is unavailable |
| `auto` | Select a qualified supported GPU at startup when available, otherwise a qualified CPU engine; report the selection |

The inspected implementation provides `cpu`/`cpu-opt` and, on macOS,
`metal`/`metal-opt`. These engine names are implementation details beneath the
public device option. Apple Metal is the current GPU path; CUDA or other GPU
support requires its own implementation and qualification.

A resident service chooses its mode at startup. Hook clients use that service's
mode; a request cannot override the daemon's engine or cause a model reload.
If a client or managed policy requires CPU and the connected server is using
GPU, reject the mismatch. Likewise, a CLI using a server must verify a supplied
`--device` constraint against readiness.

Readiness, `doctor`, decision metadata, and performance reports should include
the selected device, engine, package/branch identity, precision, and
qualification status. An explicit mode must never fall back silently.
`auto` is startup selection, not permission to swap backends after a failure.

Both modes use the same user policy, decision semantics, tokenizer, questions,
evidence offsets, and fail-closed behavior. Qualify their numerical agreement
and cutoff decisions separately. A CPU-only build should run without Metal
libraries; native Windows CPU support remains an additional portability task.
Measure CPU latency against the real hook deadline before claiming synchronous
coverage for a given model length and machine.

### Native inference review and ownership

The reviewed `Kai/infer` contains a C++17 engine with CPU and Metal backends,
trained pointer/evidence readouts, packed branch masks, and a bounded scheduler
with guard priority. Its `/v1/infer` API accepts encoded arrays and returns
logits. It currently leaves tokenization, trusted rendering, calibrated
decisions, and character evidence mapping to its caller.

Recommended ownership:

| Code/artifact | Target owner | Action |
| --- | --- | --- |
| Production `infer/src` engine, kernels, layout, and tensor reader | AgentArmor native runtime | Move as a library, retaining notices and history; harden model parsing before adoption |
| Scheduler, device coordination, and reusable protocol tests | AgentArmor native runtime | Reuse; add deadlines, package/branch routing, authentication, and native resource limits |
| Existing encoded HTTP and benchmark CLI entry points | Internal diagnostics | Replace the user-facing surface with AgentArmor decisions; do not add a second public inference hop |
| Native tokenizer, event adapters, policy/state pipeline, and readout | AgentArmor | Implement one shared native path for CLI and server |
| Training, data admission, checkpoint selection, and merged export | Kai | Keep trainer-owned |
| Canonical MLX implementation and numerical comparison tools | Kai | Keep as development/reference oracles; Python is acceptable for training and qualification |
| Export/parity fixtures and package contract | Shared versioned artifacts | Publish immutable hashes and consume them in both repositories |

Use one production source of the native engine. During migration, Kai's parity
tools may consume a pinned AgentArmor library/build or a temporary compatibility
path. Coordinate with the trainer before moving files used by active jobs.
The production snapshot has now been imported into `native/kai`; Kai's source
paths and active trainer working tree were left intact.

### Installation and release policy

The intended installation is a prebuilt arm64 binary:

```sh
curl -fsSL https://github.com/min-mwei/AgentArmor/releases/latest/download/install.sh | bash
```

The installer verifies SHA-256 and archive/architecture constraints, then
atomically installs under `~/.local/bin`. No Python, compiler, sudo, model
download, or automatic hook-settings mutation is required. Releases may
initially be unsigned. Developer ID signing is optional later; notarization and
stapling are excluded. Do not remove quarantine or alter Gatekeeper settings.
See `install.sh` and `scripts/package_macos.sh`; packaging does not publish.

### Adoption blockers

Before enabling native text policy decisions:

1. Implement native tokenizer behavior matching the pinned Kai tokenizer,
   including normalization, added/special tokens, reserved-token escaping, and
   Unicode offset maps. A roughly equivalent SentencePiece tokenizer is
   insufficient.
2. Implement strict record packing, registered question/option ordering,
   softmax, cutoff application, evidence decoding, and source-span projection.
3. Validate the merged native export against the original checkpoint; bind
   tokenizer, questions, cutoffs, profiles, length limits, and engine identity in
   a native package manifest.
4. Add AgentArmor high-level guard/observation/scan APIs and authentication.
   Keep arbitrary encoded requests internal or explicitly diagnostic.
5. Carry an end-to-end deadline through collection, queueing, inference, and
   response serialization. The diagnostic wait is now 20 seconds; it is not
   yet a qualified high-level hook deadline. Preserve guard priority and scan
   fairness.
6. Route ToolCall and XPIA to their registered checkpoints. Deduplication is
   scoped to package, branch, engine identity, and exact encoded input; a
   shared scheduler must never join requests for different models.
7. Validate tensor shapes, byte lengths, dimension products, enums, and bounds
   before loading. Preserve verified package identity through loading.
8. Provide device locking and memory/swap enforcement without a Python guard.
   An ambient environment variable must not silently disable these controls.
9. Add platform-specific file mapping, locking, IPC, and process support. The
   inspected implementation uses POSIX `mmap`/`flock`; CPU support alone does not
   establish Windows compatibility.
10. Qualify probabilities, decisions, token limits, evidence, and latency against
    canonical MLX on the trainer's scheduled device window.

The original Kai CPU build passed its two weight-free contracts. The adopted
AgentArmor build now passes nine native contracts in Release, ASan/UBSan, and
TSan configurations, with separate leak checks. See the
[recorded validation](native_security.md#validation-recorded-on-2026-10-30).
No weights were loaded; GPU/model parity and performance remain unqualified.

## Non-negotiable invariants

1. **Kai is the decision model.** AgentArmor integrates only with the official
   approximately 270M EmbeddingGemma-2 Kai encoder. **Current:** the adopted
   native engine is available for explicit encoded diagnostics; text policy
   decisions are unqualified and synchronous hooks deny. **Target:** a
   parity-qualified native frontend using the same model and readouts.
   Deterministic code supplies facts and validates
   contracts; it must not quietly replace Kai with keyword rules or another
   model.
2. **Policies belong to users and administrators.** AgentArmor may ship examples
   and recommended policy packs, but enforcement is explained in terms of the
   active, versioned policy. Project content must not silently weaken a user or
   managed policy.
3. **An allow is an abstention.** It means Kai found no violation at the
   registered cutoff. It must not bypass or auto-approve the agent's own
   sandbox, permission prompt, allowlist, or managed rules.
4. **A denial is explicit.** Synchronous policy violations, malformed input,
   unavailable inference, timeouts, unsupported encodings, oversized complete
   inputs, and unqualified renderer profiles must produce a native denial.
5. **Post-effect checks are advisory.** `PostToolUse`, file-change, stop, and
   asynchronous scans may warn, update state, or quarantine later use. They must
   never claim to have prevented the effect that triggered them.
6. **Coverage is whole or refused.** AgentArmor must not truncate a policy,
   task, event, document, history, tool argument, or relevant session state and
   then return a clear verdict. Chunked scans must cover every source character.
7. **Trust is structured.** Role labels, delimiters, source locations, hook
   fields, and tool output remain typed data. Untrusted text cannot manufacture
   a user, system, policy, or hook event by imitating transcript syntax.
8. **Evidence is grounded.** Explanations come from policy identifiers,
   deterministic facts, and Kai's pointer/evidence offsets. AgentArmor does not
   ask a generative model to invent a security rationale.
9. **Secrets stay out of diagnostics.** Hook failures, audit events, model
   reasons, and logs must not echo policy bodies, credentials, raw environment
   variables, or arbitrary attacker-controlled content.
10. **The integration boundary is testable.** CPU contract tests validate
    schemas, renderers, hook behavior, and failure paths. Actual model accuracy,
    evidence quality, calibration, and latency require registered GPU
    qualification.

## Trust and threat model

### Authority levels

AgentArmor should carry authority separately from content. A practical default
ordering is:

| Authority | Examples | May grant authority? |
| --- | --- | --- |
| Managed operator | Enterprise-managed policy, protected hook deployment | Yes, within organizational authority |
| Local user | User policy, explicit task, reviewed exception | Yes |
| Agent harness | Authenticated lifecycle metadata and permission mode | Describes state; does not create user intent |
| Agent reasoning | Plans, summaries, proposed actions | No |
| Project content | `AGENTS.md`, `CLAUDE.md`, source, README, local settings | No, unless explicitly promoted by the user |
| External content | Web pages, issues, PRs, MCP output, tool descriptions | No |
| Derived content | Compaction summaries, subagent handoffs, generated files | Inherits the least-trusted contributing source |

Trusting a repository for ordinary development does not make all of its text
equivalent to a fresh user instruction. Trusting a hook definition does not make
the workspace that invoked it trusted. These distinctions must survive every
renderer and context transition.

### Attacker capabilities in scope

Assume an attacker can control one or more of:

- repository files, nested instruction files, comments, package metadata, Git
  metadata, branch names, worktrees, symlinks, or archives;
- issues, pull requests, commit messages, web results, fetched pages, images,
  redirects, and first-party API content;
- MCP tool names, descriptions, schemas, errors, results, and server behavior;
- a dependency that writes agent configuration or instructions after install;
- content seen by one subagent and summarized for another;
- long, benign-looking text containing a small malicious instruction;
- encodings, invisible markup, Unicode confusables, or transcript-like
  delimiters; and
- timing across tool calls, context compaction, restarts, and later sessions.

Assume the attacker cannot modify a properly protected managed hook, the
AgentArmor service, or its registered Kai package. If those assumptions do not
hold, AgentArmor must report that the enforcement root is not trusted.

### Security objectives

AgentArmor should help prevent or surface:

- following lower-authority instructions that conflict with the user policy or
  task;
- sensitive reads followed by an unauthorized external sink;
- writes to agent, shell, Git, IDE, MCP, CI, or operating-system persistence
  configuration;
- permission, sandbox, approval, monitoring, or policy bypass;
- out-of-scope or out-of-workspace destructive effects;
- deceptive approval framing and source/target mismatches;
- persistent contamination of memory, compaction, handoffs, or later sessions;
  and
- use of a trusted tool or destination as a confused deputy.

### Explicit limitations

AgentArmor cannot guarantee protection when:

- a product surface does not emit a hook for the action, such as some hosted
  tools;
- hooks are disabled, untrusted, skipped, removed, or unsupported by the
  orchestration mode;
- an already-approved process performs later effects that emit no new hook;
- the hook sees a path string but the host does not supply or enforce the
  resolved filesystem object;
- an operating-system race changes an object after the check;
- a secret is already available to an untrusted process;
- an asynchronous warning arrives after a dependent action; or
- the active policy intentionally authorizes the action.

These are not reasons to omit AgentArmor. They are reasons to expose a coverage
report and pair it with sandboxing, least-privilege credentials, inert renderers,
and host-side effect enforcement.

## Architecture

### Components

| Component | Responsibility | Current status/location | Refactoring direction |
| --- | --- | --- | --- |
| Agent adapter | Decode native hook input and encode native deny/advisory output | `native/kai/server/main.cpp`; templates in `agents/` | Minimal deny/incomplete behavior implemented; add qualified event-specific adapters |
| Hook client | Bounded local I/O, authenticated transport, fail-closed response handling | Bounded stdin implemented; policy IPC absent | Add authenticated native IPC/HTTP client |
| Event normalizer | Produce a common typed security event | Not implemented | Add a versioned, lossless contract |
| Policy loader | Load, validate, layer, and identify user policies | Not implemented; examples in `agents/` | Separate policy trust from environment variables |
| Effect enricher | Add canonical path, destination, resource, and sensitivity facts | Not yet a distinct component | Add deterministic enrichers with `known/unknown` status |
| Session state | Preserve provenance and security-relevant flows across calls | Not implemented | Add bounded session/turn state and taint edges |
| Renderer | Convert structured state into a registered Kai profile | Migration fixtures in `tests/fixtures`; native implementation absent | Version per workload and preserve exact offset maps |
| Kai backend | Run the pinned encoder, pointer head, and evidence head | `native/kai/src` engine; text frontend absent | Qualify native tokenizer/readout/package path against Kai's MLX reference |
| Decision service | Authenticate, invoke Kai, apply registered cutoff, return evidence | `native/kai/server` encoded diagnostics only | Add a unified authenticated event decision endpoint; explicitly version any legacy compatibility |
| Audit sink | Record redacted decision metadata and coverage | Not implemented | Add opt-in, bounded, privacy-preserving audit events |

The split between agent-specific templates and shared evaluation behavior should
remain. The shared production behavior belongs in the native library. The
Python implementation was removed, not retained as a second policy evaluator.
Its source remains in Git history at `466a966`; synthetic renderer byte/offset
references are preserved in `tests/fixtures/renderer_migration.json`. These
fixtures do not qualify a tokenizer, checkpoint, or new event profile.

### Code ownership during the refactor

Proposed native module names below are planning targets. Python paths in the
first column identify the retired baseline at Git commit `466a966`, not files
still installed or available as an operational fallback:

| Retired baseline or source | Native destination | Attack workloads addressed |
| --- | --- | --- |
| `armor/hooks.py`, `agents/<agent>/hook.py` | `native/adapters/` plus CLI hook entry point | Native event/denial semantics for every covered action; K03–K05, K08–K13 |
| `armor/scan_client.py`, environment-backed policy/service config | `native/trust/`, `native/transport/` | Protected policy/task/service identity, no redirects, event-bound replies; K01, K07, K18 |
| `armor/schemas.py`, `armor/history.py` | `native/events/` | Typed provenance, IDs, authorization boundaries, coverage, and versioned parsing; K08, K11–K14 |
| Policy parsing/rendering in `armor/v0.py` | `native/policy/`, `native/render/` | User-authored rule precedence, complete rendering, role isolation; K01–K18 when observable |
| New effect enrichers | `native/effects/` | CWD, canonical targets, symlinks, executable identity, sink/account facts; K02–K06, K09, K15–K17 |
| `background_scan`, `armor/scans.py` | `native/observations/`, `native/state/` | Exact snapshots, pending observations, complete chunks, ordered cross-tool flows; K07–K13 |
| `armor/package.py`, `armor/kai.py`, Kai tokenizer/encoder/readout contracts | `native/model/` | Package/branch identity, native tokenization, pointer/evidence readout, strict length limits; all model workloads |
| `Kai/infer/src`, reusable scheduler/device coordination | `native/kai/`, `native/scheduling/` | CPU/Metal execution with isolated records and bounded guard latency |
| `armor/server.py`, `armor/cli.py` | `native/service/`, `native/app/` | Same high-level decisions through CLI, hooks, IPC, or authenticated HTTP |
| New prelaunch checker | `native/launch/` | Bootstrap inventory and protected invocation; K01, K02, K07, K15, K18 |

Host-only K06/K10/K14/K16/K17 paths remain external integration work where no
native event can deny them. Placing an attack in a module's workload column
defines engineering responsibility, not a prevention result.

### Synchronous decision path

A covered pre-effect event should follow this order:

1. The native adapter reads a bounded JSON object from standard input.
2. It validates the exact hook event and required fields. Unknown or malformed
   synchronous input is denied.
3. It loads policy and task from a trusted location and records their identities
   and hashes.
4. It normalizes the event without flattening roles or tool arguments.
5. Deterministic enrichers add facts the runtime can know exactly, marking
   unavailable facts as `unknown` rather than guessing.
6. The state store attaches prior untrusted sources, sensitive reads, protected
   writes, external sinks, and authorization changes relevant to this event.
7. A registered renderer constructs the complete Kai state and offset map.
8. The service refuses unencodable or oversized input before model inference.
9. Kai returns probabilities and diagnostic evidence spans.
10. The service applies only the cutoff registered with that checkpoint/profile.
11. The adapter emits a native denial or no decision. It does not emit an
    approval.
12. A redacted audit record stores the decision, coverage, package identity,
    cutoff, relevant policy IDs, and evidence locations.

### Advisory observation path

Post-effect and background events may:

- scan tool output before it is used in a later model request;
- update the session's source, read, write, sink, and persistence state;
- warn the agent or user about likely injection;
- mark later actions as dependent on suspicious content;
- submit a complete document for queued XPIA scanning; and
- produce a review artifact for operators.

They may not retroactively change the result of the completed tool. If an
advisory result must constrain the next action, that constraint is enforced by a
later synchronous pre-effect decision using the updated state.

## Hook integration

The [Codex hook contract](https://learn.chatgpt.com/docs/hooks) and
[Claude Code hook contract](https://code.claude.com/docs/en/hooks) are the
primary integration surfaces. They evolve independently, so each adapter must
declare the product version and events it has actually smoke-tested.

### Lifecycle coverage target

| Security boundary | Codex hook | Claude hook | AgentArmor behavior |
| --- | --- | --- | --- |
| Session begins/resumes | `SessionStart` | `SessionStart` | Observe policy/task/workspace identity and restore security state; this is too late to guarantee safe bootstrap |
| User submits a prompt | `UserPromptSubmit` | `UserPromptSubmit` | Record the authorization boundary; synchronously reject only when policy requires it; never treat pasted external text as authority |
| Instruction file enters context | No dedicated event | `InstructionsLoaded` | Record instruction provenance; Claude's event cannot block loading or inject a warning; gate later covered actions |
| Tool is proposed | `PreToolUse` for covered local tools | `PreToolUse` | Primary synchronous policy gate |
| Native approval is requested | `PermissionRequest` | `PermissionRequest` | Deny policy violations or abstain so the native prompt continues; never auto-approve |
| Tool succeeds/fails | `PostToolUse` | `PostToolUse`, `PostToolUseFailure` | Record actual result, scan returned content, and update state; effect is already complete |
| Parallel tool batch completes | Per-call events | `PostToolBatch` | Join related reads/writes/sinks before the next model step where available |
| Configuration changes | No dedicated event | `ConfigChange` | Claude can block settings activation in the running session where supported; the on-disk write already exists; protect writes separately |
| Watched file changes | No dedicated event | `FileChanged` | Detect out-of-band mutation; advisory unless a later pre-effect decision consumes it |
| Context compacts | `PreCompact`, `PostCompact` | `PreCompact`, `PostCompact` | Preserve taints and authorization separately from prose; scan the new summary when visible |
| Subagent starts/stops | `SubagentStart`, `SubagentStop` | `SubagentStart`, `SubagentStop` | Observe lineage and provide context; start events do not veto creation; gate the parent Agent tool and child effects |
| Model changes | No dedicated event | `PreModelSwitch`, `PostModelSwitch` | Re-evaluate whether the current security profile is qualified for the new model/harness behavior |
| Turn/session stops | `Stop`, `SessionEnd` | `Stop`, `StopFailure`, `SessionEnd` | Flush redacted state and queue final advisory scans |

**Current:** the supplied templates install `PreToolUse`, `PostToolUse`,
`UserPromptSubmit`, and `Stop` using the native executable directly.
`PreToolUse` explicitly denies every check while the frontend is unqualified.
The other events return an incomplete-observation message; native content
scanning and durable queueing are not implemented. `PermissionRequest` denial
serialization exists in the CLI but is not enabled by these templates.

**Target:** adapters should use all security-relevant events available on the
installed client, while maintaining a documented fallback for missing events.
Adding an event requires native contract fixtures and live smoke tests; an event
name alone is not evidence of coverage.

### Native decision mapping

For both adapters:

| AgentArmor result | Hook behavior |
| --- | --- |
| `deny` | Return the product's supported explicit denial shape before execution |
| `allow` | Return no permission decision; native permissions continue |
| invalid request or response | Explicit denial for synchronous events |
| timeout or service unavailable | Explicit denial for synchronous events |
| advisory suspicious result | Add a warning/context message; do not claim prevention |
| advisory failure | Report incomplete scan; do not emit a clear verdict |

Codex explicitly documents that a malformed or failed `PreToolUse` hook can
allow the tool call to continue. The adapter therefore must catch its own
failures and emit a supported denial shape (or exit code 2 as a final native
fallback), not rely on hook-process failure. It must also avoid unsupported
output fields that Codex treats as a hook error.

The executable cannot emit denial JSON if it never starts, is killed, or is
terminated by the host hook timeout. On supported
Claude versions, set `onFailure: "block"` for synchronous command hooks and test
it. Codex's documented hook failures can continue into native permissions, so
independent sandbox/managed controls remain necessary. A service's socket
timeout alone is not an end-to-end deadline.

### Bootstrap and unobserved effects

A trusted launcher or host integration must check executable configuration
before the agent starts loading the project. `SessionStart` cannot protect
against a sibling startup hook, and instruction-load observation cannot prevent
the instruction from entering context. Automatic Git helpers, plugin loading,
render-time requests, `/copy`, IDE WebSockets, existing process input, and
operating-system services need their own coverage assessment.

The launcher must inspect files without invoking project-controlled Git,
package managers, plugins, or scripts. It must use a protected executable,
installation, service endpoint, policy, and package identity. A launch check
reduces bootstrap risk but cannot close a later filesystem race; a vulnerable
host still needs its vendor fix.

### Hook installation and trust

- Prefer user-level or managed hooks installed outside an untrusted repository.
- Enterprise deployments should use managed hook mechanisms where available and
  protect the installed script and policy file from the agent account.
- Project-local hook configuration is suitable only after explicit review. A
  repository must not be able to replace the enforcement script it is meant to
  be checked by.
- Bind trust to content identity. A changed hook or policy should require review
  or fail closed.
- The native hook client must authenticate the service and disable ambient
  proxy use; authenticated policy transport is still to be implemented.
- Pin the service origin and reject redirects. Require authenticated TLS for
  non-loopback deployment; pin the expected package/profile and policy identity.
- Bind a response to its event, action hash, policy/task hashes, and state
  revision. A bare `{"decision": "allow"}` is insufficient for the target client.
- Use an absolute, trusted executable path. Protect the executable, libraries,
  and hook configuration; a read-only file owned by the
  agent's own account is not a strong isolation boundary.
- Hooks and the service must not inherit more credentials than required.
- Installation must include a smoke test that proves a known deny reaches the
  native client before a harmless tool effect.
- Coverage reports should identify hosted, cloud, or remote surfaces where the
  local hook is not running.

The official OpenAI plugin format can bundle lifecycle hook configuration for
manual Codex installations, but bundled hooks still require trust and local
scripts; see [OpenAI's plugin packaging guidance](https://developers.openai.com/plugins/build/plugins).
Distribution convenience does not remove the bootstrap trust problem.

## Policy model

### Policy is trusted data, not executable configuration

A policy states what the user or administrator authorizes. It must not contain
shell fragments, templated hook commands, or executable plugins. The policy
loader treats its prose as trusted model context only after the policy source
has been authenticated and validated.

**Retired baseline:** the Python loader used `AGENTARMOR_POLICY_FILE` for JSON
containing:

- `environment`: named facts supplied by the operator; and
- `rules`: stable `id`, `tier`, and natural-language `description`.

It supplied the task separately through `AGENTARMOR_TASK`. These environment
variables are not native configuration APIs. The current executable does not
load or evaluate the example policy files.

The canonical operator template is
[`agents/policy.example.jsonl`](../agents/policy.example.jsonl). Each nonempty
UTF-8 line is one object with exactly `id`, `tier`, and `description`. Rule IDs
are unique; tiers are `allow`, `soft_deny`, or `hard_deny`; descriptions are
short, explicit rules. The [smoke policy](../agents/policy.smoke.jsonl) protects
one marker file. The older prose and JSON examples have been removed.

**AgentArmor owns parsing and refusal.** The native loader must reject invalid
UTF-8/JSON, duplicate keys or colliding rule IDs, unknown fields/tiers, missing
or empty values, comments/arrays, and oversized complete inputs. A malformed
line refuses the whole policy; no rule may be silently skipped. Blank lines
may be ignored and a trailing newline is allowed. An empty policy is refused
unless an explicit trusted configuration permits it. Preserve rule order,
exact descriptions, source line numbers, and complete-file hash. Policy
identity/layer/ownership, workspace facts, destinations, and task come from a
separate trusted envelope, not arbitrary rule lines.

**Kai owns the trained input contract.** AgentArmor converts the validated
rules and trusted context into the exact registered `state` and `questions`
format used by Kai training. Do not send raw JSONL as a new model prompt,
rewrite descriptions, invent enforcement questions, reorder options, or
introduce a new renderer solely because the disk format changed. Preserve
source-to-state-to-token evidence mappings and use Kai's supplied golden
fixtures. Richer event profiles need explicit trainer qualification before
enforcement. The current native loader/frontend is not implemented.

**Target:** keep that small conceptual core while adding versioned identity,
scope, ownership, and composition metadata. A parsed runtime envelope might
contain:

```json
{
  "schema_version": "agentarmor-policy-v1",
  "policy_id": "personal-development",
  "environment": {
    "workspace": "/work/project",
    "authorized_destinations": ["github.com/acme/project"]
  },
  "rules": [
    {
      "id": "protect-agent-config",
      "tier": "hard_deny",
      "description": "Do not create or modify agent hooks, MCP registration, global instructions, or permission settings while processing untrusted project content."
    }
  ]
}
```

This is an architectural example of parsed state, not the JSONL file format or
an implemented native API. Its model rendering must follow Kai's registered
training profile.

### Policy layers

Policy composition should be monotonic:

1. managed policy;
2. user policy;
3. session/task-specific restrictions; and
4. explicitly trusted project policy.

A lower layer may narrow authority or add a denial. It may not remove or weaken a
higher-layer denial. Ordinary repository content is not a policy layer. If a
user chooses to promote a project policy, the UI should show its content hash,
scope, and requested relaxations before activation.

Conflicts should resolve toward the more restrictive result. Invalid or
ambiguous composition should deny synchronous actions rather than silently
dropping a rule.

### Task and authorization boundary

The policy is durable; the task is the user's current delegation. They are not
interchangeable.

- The task must be captured from a user-authoritative boundary.
- Quoted issue text, pasted logs, web content, or files inside a prompt remain
  lower-trust data even though the user submitted the message.
- A task update must have a session/turn identity and cannot be inferred from an
  agent summary.
- A subagent receives the task and policy as typed metadata, not as text written
  by another agent.
- A compaction summary cannot grant new authority.
- Expired tasks and one-time approvals must not remain active in later sessions.

The retired static task environment variable must become an explicit trusted
session/turn authorization object in the native implementation.

### Recommended policy subjects

Policies should be able to express, in natural language backed by structured
scope facts:

- protected resources and workspace boundaries;
- secret and private-data handling;
- authorized network, repository, account, and publication destinations;
- destructive operations and recovery requirements;
- agent configuration, hooks, MCP, skills, instruction, memory, Git, shell,
  IDE, and CI persistence;
- use of bypass, unrestricted, or nested-agent modes;
- provenance rules for project, web, issue, tool, and MCP instructions;
- cross-project and privilege escalation;
- package install and executable provenance;
- behavior when required effect facts are unknown; and
- whether a rule is a hard deny, a soft deny subject to explicit user
  authorization, or an affirmative allowance.

AgentArmor should provide reviewed templates for common policies, but activating
or modifying them remains a user or administrator action.

## Typed security-event contract

Flattened transcripts are vulnerable to role spoofing and lose facts needed for
policy. The shared event envelope should be versioned and preserve native fields:

```json
{
  "schema_version": "agentarmor-event-v1",
  "event_id": "opaque-id",
  "agent": "codex_code",
  "agent_version": "reported-version",
  "session_id": "native-session",
  "turn_id": "native-turn",
  "event": "PreToolUse",
  "phase": "pre_effect",
  "permission_mode": "default",
  "cwd": "/work/project",
  "policy_ref": "sha256:...",
  "task_ref": "turn:...",
  "origin": {
    "kind": "model_proposal",
    "influenced_by": ["source:issue-123"]
  },
  "action": {
    "tool_name": "Bash",
    "tool_input": {"command": "git status"}
  },
  "effect_facts": [],
  "history": []
}
```

The exact public schema should be designed with compatibility tests before
implementation. Required semantics are:

- all native IDs and modes are opaque, typed values;
- tool arguments remain JSON rather than interpolated prose;
- content fields have source, author/owner, trust, acquisition event, and hash;
- paths keep both literal and canonical representations when known;
- external destinations keep requested and final ownership information;
- unknown facts are explicit;
- raw source offsets map into Kai's rendered state and back; and
- the envelope is bounded without silently dropping fields.

## Deterministic effect facts

Kai should reason over semantic intent and policy. The runtime should supply facts
it can determine more reliably:

| Fact | Examples |
| --- | --- |
| Filesystem identity | Literal path, canonical path, containing root, symlink/junction status, protected-resource class |
| Process effect | Executable identity, argument vector when available, effective user, sandbox mode, inherited credential classes |
| Configuration effect | Agent config, hook, MCP, instruction, memory, Git helper, shell startup, CI, IDE, or service persistence |
| Network effect | Requested host, resolved/final host, redirect chain, method, destination owner, public/private sink |
| Data sensitivity | Credential file class, environment/process metadata, private repository, user-selected sensitive label |
| Provenance | User, managed policy, project, web, issue/PR, MCP server, tool result, agent-generated, summary |
| Temporal state | Previously ingested untrusted content, sensitive read, pending external sink, prior config mutation |

An enricher is not an authorization engine. It may classify a path as "agent
configuration" or say that a symlink exists; Kai and the active policy decide
whether the proposed action is allowed. If a fact cannot be obtained without
causing the effect, it remains unknown.

AgentArmor cannot close a TOCTOU race merely by resolving a path. The host must
enforce no-follow or handle-bound operations at use time. The policy should be
able to deny when that enforcement is unavailable.

## Stateful provenance and dataflow

Many attacks in the research corpus are harmless-looking one call at a time.
AgentArmor therefore needs bounded security state across calls.

At minimum, track:

- untrusted sources admitted to context and their hashes;
- policy/task versions and explicit authorization changes;
- sensitive resources read or made reachable;
- protected or persistent resources written;
- external/public sinks proposed or used;
- MCP servers, tool descriptions, and schemas seen in the session;
- instructions, config, or memory modified by an agent;
- subagent and compaction lineage;
- synchronous decisions and advisory scan status; and
- causal links the harness exposes between a source and an action.

The most important compound condition is:

```text
untrusted influence
  + sensitive data or privileged capability
  + external or persistent sink
  = require a synchronous, narrowly scoped authorization decision
```

State must not be a raw transcript cache. Store compact typed facts, hashes, and
references. Raw content should be retained only when required for the immediate
complete decision and according to an explicit privacy setting.

### Ordering and exact content

Before background inference starts, a synchronous observation step must register
the tool result or instruction source, its hash, and a pending scan in the state
store. A dependent `PreToolUse` evaluates the complete required context or waits
within its deadline for that observation. It denies when required coverage cannot
be established. Launching an asynchronous hook does not establish this ordering.

Store the exact model-visible tool result as its own source. A later file reread
is a different snapshot and cannot clear the earlier result; a malicious file
can change between read and scan. When policy requires whole-file coverage, scan
a complete, stable snapshot in addition to the returned range, or refuse that
claim.

Use atomic state revisions and pending-effect reservations for parallel calls.
Bind decisions to those revisions and invalidate them when policy, task,
resource identity, or relevant state changes. Share state between authorized
parent/child sessions and sessions accessing the same protected resources.
Never infer proven dataflow solely from a model explanation; distinguish
observed dependency, conservative possible influence, and unknown linkage.

### Compaction and subagents

- Preserve policy, task, provenance, taint, and protected-state facts outside
  natural-language compaction.
- Scan summaries before treating them as context, but never use a clean scan as
  proof that provenance was preserved.
- A handoff identifies its authoring agent and source dependencies.
- Subagents inherit restrictions, not ambient permissions.
- A later model or resumed session must see outstanding suspicious/persistent
  state before its first effect.
- Agent-authored "the user approved this" text never changes authorization
  state.

## Decision workloads

### Tool authorization

The synchronous tool decision includes the active policy, current task,
chronological typed history or security state, the proposed native tool call,
permission mode, and deterministic effect facts. Kai's registered direct choice
returns `allow` or `deny` probabilities. AgentArmor applies the checkpoint's
registered cutoff.

The service reason should identify:

- the decision and score/cutoff;
- policy rule IDs or categories that were evaluated;
- relevant deterministic facts; and
- mapped evidence locations when available.

It must not expose raw secrets or model-generated prose.

### Content and instruction scanning

The XPIA branch scans project files, instructions, tool output, web content, MCP
metadata/results, chat logs, compaction summaries, and handoffs. There are two
different uses:

- **Pre-ingestion synchronous scan:** block or quarantine content before it can
  influence a later action when the hook supports that boundary.
- **Post-ingestion advisory scan:** warn and taint state when the content has
  already entered context.

A document-only profile cannot claim task- or policy-aware protection. A
contextual profile must be separately trained, registered, and calibrated.

### Factorized risk questions

A future qualified checkpoint may expose stable questions for authority
conflict, control bypass, persistence, sensitive access, external sink,
deception, scope escalation, and compound flow. These signals help policy
explanation and evaluation, but the final synchronous decision remains a
registered direct policy choice. Details belong in
[Kai requirements](https://github.com/min-mwei/Kai/blob/dev/docs/Kai_requirements.md).

## Mapping attack families to controls

| Attack family | AgentArmor contribution | Required external control |
| --- | --- | --- |
| Pre-trust project config and MCP startup | Session/config hooks, protected-config policy, provenance, pre-effect denial | Agent must not load or execute project config before trust |
| Safe-command parser differential | Kai sees exact tool input plus effect uncertainty; deny bypass intent | Harness must avoid shell interpolation and enforce sandbox policy |
| Symlink, junction, and TOCTOU | Canonical/literal path facts, protected-target policy, deceptive-approval detection | Descriptor/handle-bound no-follow writes at execution |
| `AGENTS.md`/`CLAUDE.md` indirect injection | Instruction-load or content scan, lower-authority label, dependent-action gate | Product must expose ingestion hooks and preserve source |
| Trusted-domain or renderer exfiltration | Sensitive-source plus sink state, destination ownership, output scan | Exact egress controls, inert rendering, redirect enforcement |
| MCP tool poisoning and shadowing | Scan/pin descriptions and schemas; track cross-tool flow | MCP client identity, content pinning, least-privilege credentials |
| CI issue/PR injection | Policy for untrusted contributions, no-secret state, synchronous tool gate | Secretless or narrowly scoped runner and write token |
| Compaction/monitor injection | Typed events, escaped delimiters, externalized security state | Harness must deliver complete structured events |
| Agent-config persistence | Protected-resource write denial, config-change observation, next-session state | Protect config files and review hook/MCP changes |
| Malicious repository binary | Provenance/deception reasoning and executable policy | Code signing, sandboxing, malware controls |
| Web/custom-scheme prompt submission | Origin and authorization metadata; prompt policy | Product origin authentication and informed preview |

AgentArmor is strongest where semantic intent and sequence matter. The external
column is equally important: a model decision cannot repair a host boundary that
the hook cannot observe or enforce.

## API direction

### Current endpoints

The native server exposes encoded `/v1/infer` diagnostics and readiness/model
metadata, restricted to loopback and explicit `--encoded-only` startup. Its
readiness reports `policy_ready: false`.

The removed Python service provided `/v1/guard`, `/v1/scans`, and
`/v1/systemone`. They are not implemented by the native server. Reintroduce any
needed compatibility explicitly over the qualified native evaluation library;
do not revive Python serving as an integration dependency.

### Target shared event endpoint

A future `POST /v1/evaluate` can accept the versioned event envelope and return:

```json
{
  "decision": "allow",
  "enforcement": "abstain",
  "model": "kai",
  "package_sha256": "sha256",
  "profile": "armor-event-v2",
  "risk_score": 0.01,
  "cutoff": 0.5,
  "evidence_status": "diagnostic",
  "evidence": [],
  "coverage": {
    "complete": true,
    "native_surface": "covered"
  }
}
```

This shape is illustrative. Before implementation, define:

- which events can produce an enforcement decision;
- session-state ownership and concurrency;
- idempotency and event ordering;
- authenticated agent/service identity;
- maximum sizes and refusal semantics;
- redaction and retention; and
- compatibility with `/v1/guard`.

An observation endpoint may accept post-effect events without pretending they
are decisions. Queued scans should remain separate from synchronous enforcement
so queue saturation cannot be mistaken for a clean result.

## Failure behavior

| Failure | Synchronous path | Advisory path |
| --- | --- | --- |
| Missing policy/task | Deny | Mark incomplete |
| Malformed native event | Deny | Mark incomplete |
| Oversized or unencodable complete input | Deny | Fail job; no clean result |
| Unsupported renderer/profile | Deny | Fail job |
| Service timeout/unavailable | Deny | Warn/record incomplete |
| Kai runtime/package mismatch | Service not ready; deny | Fail job |
| Evidence head unavailable | Decision may proceed only if that package is qualified; mark evidence unavailable | Mark unavailable |
| Hook output cannot be encoded | Use minimal native denial fallback | Minimal warning if possible |
| State order conflict or missing prerequisite | Deny when relevant to authorization | Record degraded coverage |
| Scan queue full | Does not affect a separate synchronous decision | Reject submission explicitly |

Do not include exception strings derived from attacker input in native hook
messages. Detailed diagnostics belong in protected operator logs.

## Privacy, logging, and audit

Default audit records should contain:

- timestamp and opaque event/session/turn IDs;
- agent and adapter version;
- policy/task hashes, not bodies;
- tool/effect categories and redacted resource identifiers;
- decision, probability, cutoff, profile, and package identity;
- coverage and failure status;
- policy IDs and evidence offsets into ephemeral canonical state; and
- latency and retry information.

Raw prompts, transcripts, file contents, tool output, environment variables, API
keys, and full paths should be opt-in and short-lived. Logs must never become a
new prompt-injection or credential-exfiltration channel. An audit store should
have separate credentials and should not be writable through ordinary agent
tools.

## Deployment profiles

### Personal/local

- User-level trusted hooks.
- Policy outside the working repository.
- Local authenticated AgentArmor service.
- Explicit task per session.
- Coverage report displayed at startup.

### Team repository

- User or managed hooks, not repository-replaceable scripts.
- Versioned team policy plus user restrictions.
- Project policy can add restrictions after review.
- Shared Kai package identity and evaluation card.
- CI smoke tests for adapter contracts.

### Enterprise/CI

- Managed hooks and protected binaries.
- Service authentication and short-lived client identity.
- Managed policy that lower layers cannot weaken.
- Secretless untrusted jobs wherever possible.
- Central redacted audit with retention controls.
- Pinned client versions and staged hook-contract rollout.
- Deny when a required lifecycle surface is unavailable.

Remote/cloud agent modes need separate qualification. A local hook template does
not protect an execution environment in which it is never invoked.

## Refactoring roadmap

### Phase 1: freeze contracts and protect the current boundary

- Protect the hook executable, installation, policy, service origin, and Kai
  package identity before adding more model input surfaces.
- Reject service redirects and bind every decision to the submitted event.
- Introduce versioned native event fixtures for both agents.
- Preserve captured renderer byte/offset fixtures and obtain the complete
  tokenizer/readout golden set from Kai; the Python baseline is in Git history.
- Record hook capabilities and unsupported surfaces.

**Exit criterion:** complete native event/renderer fixtures and explicit
allow/deny/failure semantics exist before any runtime replacement.

### Phase 2: adopt the native engine as a library

- Bring the reviewed production engine, scheduler, and weight-free tests into
  AgentArmor with provenance and notices.
- Keep training/export/MLX references in Kai and agree on a versioned export.
- Harden tensor/config parsing and native device/resource controls.
- Add per-request deadlines and package/branch-safe scheduling.

**Exit criterion:** the native library builds and passes weight-free layout,
protocol, scheduler, malformed-package, and resource-control contracts.

### Phase 3: complete native decisions and hook transport

- Implement tokenizer/offset parity, record encoding, softmax, calibrated
  readout, evidence mapping, and package verification.
- Implement `agentarmor hook`, `check`, `serve`, `scan`, and `doctor` over one
  evaluation library.
- Port both native hook adapters and preserve `/v1/guard` compatibility where
  needed.
- Prove that the installed runtime invokes no Python.

**Exit criterion:** tokenizer golden fixtures, trainer-owned model parity,
native-client deny/abstain tests, and end-to-end deadlines pass before switching
the default serving path.

### Phase 4: policy identity and richer events

- Add policy version/source/hash, monotonic composition, and trusted task
  updates.
- Preserve CWD, permission mode, session/turn/tool IDs, and platform facts.
- Add permission, instruction/config, compaction, and subagent observations with
  their actual native capabilities.
- Add protected-resource, canonical-path, executable, and sink facts; mark
  missing facts explicitly.
- Define the new renderer jointly with the trainer; keep the old profile frozen.

**Exit criterion:** source-trust and policy-composition contracts pass, and the
trainer receives the exact event/rendering shapes the native runtime can supply.

### Phase 5: stateful provenance and flows

- Add bounded per-session security state with event ordering.
- Register pending observations synchronously and gate dependent actions;
  preserve exact tool-result snapshots rather than clearing them with rereads.
- Use revisions/reservations for concurrent calls.
- Carry sensitive sources and persistent changes through compaction, subagents,
  resume, and restart.
- Add expiration, coverage/exhaustion behavior, and privacy controls.

**Exit criterion:** complete toxic-flow, stale-snapshot, concurrency, and
persistence episodes are represented without missing required facts.

### Phase 6: expand qualified Kai workloads

- Train and register the event/contextual profiles described in
  [Kai requirements](https://github.com/min-mwei/Kai/blob/dev/docs/Kai_requirements.md).
- Keep held-out attack examples separate from training; author independent
  examples from abstract tactic briefs.
- Compare the direct decision baseline before adding auxiliary questions.
- Qualify the merged native export on CPU/Metal with frozen cutoffs.

**Exit criterion:** trainer-owned GPU qualification meets predeclared attack,
benign, calibration, evidence, parity, and latency gates; native end-to-end
attack replays confirm each claimed enforcement boundary.

### Phase 7: hardened distribution

- Ship platform binaries, checksummed releases (initially unsigned), model-package manifests,
  and trusted user/managed hook installers.
- Implement the `run` prelaunch path without invoking untrusted project tools.
- Provide native daemon/service lifecycle and IPC permissions.
- Publish platform and hook coverage separately for local, desktop, cloud, CI,
  CPU, and Metal.
- Keep production serving native. Python serving has already been removed;
  optional dev/test scripts may remain, and Kai owns MLX reference tooling.

**Exit criterion:** installation cannot silently claim protection when hooks are
untrusted, disabled, unsupported, or absent, and end users need no Python runtime.

## Testing strategy

### CPU contract tests

Cover:

- every native event schema and decision output shape;
- explicit denial on all synchronous client failures;
- allow-as-abstention;
- full-input size/encoding refusal;
- delimiter escaping and role isolation;
- policy composition and source trust;
- event ordering and idempotency;
- canonical/evidence offset projection with Unicode;
- scan chunk overlap and whole-source coverage;
- redaction and bounded audit records; and
- lazy GPU imports.

### Live agent tests

For each supported Codex and Claude version:

- prove a benign call continues to native permissions;
- prove a harmless marker write is denied before creation;
- prove hook failure emits a denial rather than falling through;
- verify permission, compaction, and subagent events actually fire;
- verify hosted/uncovered tools are reported as gaps;
- verify advisory output timing; and
- verify the installed hook cannot be replaced by the test repository.

### GPU/model qualification

CPU tests do not qualify Kai. Use the trainer-owned registered evaluation
described in [Kai requirements](https://github.com/min-mwei/Kai/blob/dev/docs/Kai_requirements.md), and report model results
separately from adapter contract results.

## Definition of done for an AgentArmor feature

A feature is not complete until:

1. its security boundary and trust assumptions are documented;
2. its native hook coverage is proven on each claimed client;
3. its input is complete, typed, bounded, and provenance-preserving;
4. its renderer/question/cutoff is registered with the Kai package;
5. synchronous errors deny and allow results abstain;
6. post-effect behavior is labeled advisory;
7. evidence offsets map back to caller-owned text;
8. CPU contract tests pass;
9. required GPU/model qualification passes; and
10. limitations and uncovered surfaces are visible to the user.

## Open design decisions

The refactor should resolve these explicitly rather than letting implementation
details decide them:

- How does a user update the authorized task during a session without letting
  agent-authored text grant authority?
- Which policy relaxations require an interactive review, and how are they
  represented when native hooks cannot return `ask`?
- Which security state persists across restart, for how long, and under whose
  encryption/retention policy?
- Which deterministic effect facts can each agent reliably supply today?
- How should AgentArmor quarantine suspicious content when a hook can block a
  tool but cannot remove text already in model context?
- What minimum hook coverage is required before the product may call a session
  "protected"?
- How are cloud and hosted-tool actions integrated when local lifecycle hooks do
  not observe them?

Until these are answered and implemented, the product should describe coverage
precisely: a policy gate for covered hook events, powered by Kai, with advisory
scanning around the remaining surfaces.

For individual CVEs and research reports, use the
[attack-to-workload matrix in the trainer requirements](https://github.com/min-mwei/Kai/blob/dev/docs/Kai_requirements.md#attack-to-workload-matrix).
Its coverage classifications apply to the runtime roadmap as well as the model.
