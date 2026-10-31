# Recent attacks on Codex and Claude agents

> Research snapshot: 2026-10-30. This document covers public disclosures from
> 2025-04-01 through 2026-10-30, with emphasis on Codex CLI/Desktop/Cloud and
> Claude Code/Desktop/Cowork. Dates are public-report or advisory dates, not
> necessarily the date of discovery. Examples are intentionally non-weaponized.

This is a threat-modeling document for AgentArmor, not an exploit guide. The word
"attack" is used broadly for confirmed incidents, vendor advisories, reproducible
proofs of concept (PoCs), and empirical red-team results. Those evidence levels
are kept separate below.

## Scope and evidence labels

Included:

- attacks against the agent product or its orchestration layer;
- attacks delivered through repositories, instruction files, issues, web pages,
  MCP servers, tool output, memory, compaction, or CI metadata;
- conventional vulnerabilities that cross an agent trust boundary, such as a
  path-check bug that turns an approved edit into an out-of-workspace write; and
- adjacent Claude surfaces when they exercise the same agent boundary, including
  Claude Desktop, Cowork, claude.ai, and `claude-code-action`.

Excluded unless they illuminate that boundary:

- attacks *performed with* Claude or Codex against unrelated third parties;
- generic model jailbreaks and insecure generated code;
- vulnerabilities in arbitrary third-party MCP servers with no demonstrated
  Codex or Claude path; and
- malware that only impersonates the product and never interacts with the agent.

Evidence labels used in this document:

| Label | Meaning |
| --- | --- |
| **Incident** | Malicious artifacts or activity were observed outside a lab. This does not necessarily prove the full intended chain completed. |
| **Vendor advisory** | A vendor or CNA published a security advisory/CVE. |
| **PoC** | Researchers demonstrated the attack in a controlled environment. |
| **Empirical** | A benchmark or repeated red-team evaluation measured susceptibility. Results are version- and setup-specific. |
| **Disputed** | The researcher reproduced a behavior, but the vendor classified it as expected, out of scope, or not reproducible. |

### Research method and coverage limits

The advisory ledgers were built from the public GitHub security-advisory records
for `openai/codex`, `anthropics/claude-code`, and
`anthropics/claude-code-action`, then cross-checked against CVE/NVD records and
the linked vendor or researcher reports. Non-advisory entries favor original
research, incident reports, or technical papers over secondary coverage. Version
ranges and fix versions are those stated by the source on the snapshot date.

"All attacks" can only mean all relevant *publicly documented* attacks found in
that process. Private incidents, undisclosed reports, and newly published
advisories will not appear. The corpus should therefore be refreshed regularly;
changes in evidence or vendor status should be preserved rather than silently
rewriting the historical record.

## Executive findings

The public record does not support treating prompt injection as only a bad string
that can be filtered before it reaches the model. The recurring attack chain is:

```text
attacker-controlled artifact
  -> artifact is promoted from data to authority
  -> model selects a legitimate tool or "safe" command
  -> approval, path, sandbox, or egress control sees an incomplete representation
  -> write, execution, or data transfer occurs
  -> config/memory changes persist the compromise into later sessions
```

The most important conclusions for AgentArmor are:

1. **Repository text is an execution surface.** `AGENTS.md`, `CLAUDE.md`, hooks,
   settings, MCP definitions, Git metadata, package scripts, comments, and ordinary
   READMEs have all been used as control inputs. Trusting a directory is not the
   same as authorizing every instruction or executable configuration within it.
2. **Approvals fail when they describe syntax rather than effects.** Safe-command
   parsers, literal paths, and simplified tool UIs repeatedly disagreed with the
   shell, filesystem, Git, or network behavior that followed.
3. **An allowlisted sink is still an exfiltration sink.** Trusted domains, remote
   Markdown images, first-party file APIs, GitHub comments, public pull requests,
   logs, and model API base URLs have all carried secrets.
4. **Prompt injection is often only the first stage.** The damaging stage is a
   deterministic boundary failure: an uncanonicalized path, pre-trust config load,
   overbroad credential, auto-started MCP server, exposed CI environment, or
   unmonitored context transition.
5. **Persistence changes the risk.** Attacker-written agent config, hooks, MCP
   registrations, memory, and compaction summaries can outlive the input that
   caused them. A later model, subagent, or session may be less resistant.
6. **CI removes the human brake.** Auto-trusted workspaces and non-interactive
   modes turn one-click PoCs into zero-click chains, while runners often hold
   repository, registry, cloud, and signing credentials.
7. **Model-only firewalls are insufficient.** The strongest public results use
   semantic camouflage, forged monitor transcripts, multi-context decomposition,
   or legitimate tools. Deterministic least privilege and effect-aware enforcement
   are required alongside semantic detection.

## Attack-surface map

| Surface | Attacker-controlled examples | Common transition | Observed objective |
| --- | --- | --- | --- |
| Project instructions | `AGENTS.md`, `CLAUDE.md`, README, source comments, skills | Data is treated as project-owner instruction | Run code, hide a change, alter review output |
| Project configuration | `.claude/settings.json`, `.mcp.json`, `.codex/config.toml`, `.env` | Config is loaded before trust, or trusted by location rather than content | Startup RCE, persistence, API endpoint replacement |
| Tool protocol | MCP tool descriptions, schemas, errors, results | Tool metadata/output enters the privileged context | Tool poisoning, shadowing, cross-tool exfiltration |
| Repository metadata | Branch names, `.git/config`, worktrees, hooks, filters | Metadata is interpolated into a shell or Git launches a helper | Token theft, unsandboxed RCE |
| Filesystem | Symlinks, junctions, path prefixes, races, predictable temp files | Check and use resolve to different objects | Out-of-workspace write, sandbox escape, privilege escalation |
| Shell approval | `rg`, `sed`, `find`, `echo`, PowerShell, redirections | Validator and runtime parse the same bytes differently | Approval bypass, arbitrary write or execution |
| Network and rendering | Web search, WebFetch, remote images, first-party APIs, redirects | Content enters context or output causes an automatic request | Indirect injection, blind data exfiltration |
| Collaboration and CI | Issues, PRs, comments, workflow metadata, logs | Untrusted contributor text drives a secret-bearing runner | Secret theft, malicious PR, supply-chain change |
| Long-lived context | Memory files, compaction, subagent handoffs, shared workspaces | Provenance/intent is lost across a context boundary | Persistent steering, monitor bypass |
| UI/local integration | IDE WebSockets, custom URL schemes, browser extensions | A web origin can submit prompts or call a local privileged service | File read, browser takeover, one-click/zero-click execution |

## Detailed attack chains

### 1. Project-local configuration executes before meaningful consent

**Codex — project-local MCP execution.** Check Point disclosed
[CVE-2025-61260](https://research.checkpoint.com/2025/openai-codex-cli-command-injection-vulnerability/)
on 2025-12-01 after reporting it on 2025-08-07. A repository could use a local
`.env` to redirect `CODEX_HOME` into the repository and supply a
`.codex/config.toml` containing an MCP server command. Starting Codex in the
repository launched the command without a second approval. The researchers used
benign file creation and a calculator as demonstrations, then validated that the
same path could launch a reverse shell. OpenAI fixed the redirection path in CLI
0.23.0 on 2025-08-20.

**Claude — hooks, MCP, and model endpoint replacement.** Check Point published a
combined report on 2026-02-25 covering
[CVE-2025-59536 and CVE-2026-21852](https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/).
The demonstrated chains were:

- a `SessionStart` hook in repository-controlled settings executed after a broad
  folder-trust click without a distinct command approval;
- project settings enabled all project MCP servers, so the MCP command ran before
  the pending trust dialog could be acted on; and
- `ANTHROPIC_BASE_URL` in project settings redirected startup API traffic,
  including authorization, to an attacker endpoint before trust confirmation.

[CVE-2026-33068](https://github.com/anthropics/claude-code/security/advisories/GHSA-mmgp-wc2j-qcv7)
later showed the same ordering problem in a different form: repository settings
could select `bypassPermissions` before Claude Code decided whether to show the
workspace-trust dialog.

**Why it matters.** A trust decision must be made before *parsing*, resolving,
starting, or contacting anything derived from the project. Trust should bind to a
reviewed content hash and capability set, not merely a path or server name.

### 2. Repository metadata crosses into a privileged shell or Git helper

BeyondTrust published a Codex Cloud command-injection chain on 2026-03-30. The
[branch name was interpolated into environment setup shell commands](https://www.beyondtrust.com/blog/entry/openai-codex-command-injection-vulnerability-github-token).
GitHub branch-name rules blocked literal spaces but allowed shell syntax that
expanded into spaces; Unicode whitespace could also make the malicious suffix
less visible. The injected command exposed the authenticated Git remote, and the
agent was then asked to read the resulting token-bearing file. An automated
variant could target users who interacted with a malicious branch or invoked
Codex review from GitHub. OpenAI classified the report P1 Critical and applied
fixes between 2025-12-23 and 2026-01-30.

Three September 2026 Codex advisories show that avoiding explicit shell
interpolation is not enough when Git itself can execute repository-configured
programs:

- [CVE-2026-19590](https://nvd.nist.gov/vuln/detail/CVE-2026-19590):
  `core.hooksPath` launched attacker-controlled Git hooks;
- [CVE-2026-19592](https://nvd.nist.gov/vuln/detail/CVE-2026-19592):
  `core.fsmonitor` launched a filesystem-monitor helper; and
- [CVE-2026-19593](https://nvd.nist.gov/vuln/detail/CVE-2026-19593):
  `attr.tree` plus clean/process filters launched a program during automatic
  workspace inspection.

These three chains required a repository delivered with its local `.git/config`
intact; an ordinary clone does not copy that file. The distinction is important:
the attack is not "any malicious GitHub repository," but an archive, shared
folder, synced workspace, removable media, or other delivery that preserves
attacker-controlled local Git metadata.

**Why it matters.** Any metadata consumer must be analyzed for callbacks and
helper execution. AgentArmor needs provenance on fields such as branch names and
Git config, even when they do not look like natural-language prompts.

### 3. "Safe command" parsing disagrees with the real interpreter

Both products repeatedly attempted to auto-approve a subset of shell commands.
The attack pattern is a parser differential: the policy layer concludes that a
command is read-only, while the actual program or shell interprets an option,
redirection, pipeline, environment expansion, or token boundary as executable or
writable.

Examples include Codex
[CVE-2025-54558](https://nvd.nist.gov/vuln/detail/CVE-2025-54558), where
`ripgrep` options capable of invoking preprocessors were still auto-approved, and
[CVE-2026-19591](https://nvd.nist.gov/vuln/detail/CVE-2026-19591), where the
safety parser and PowerShell disagreed about the `--%` stop-parsing token. The
latter could turn a nominally safe Git write into persistent Codex configuration
and then an attacker-controlled MCP server.

Claude Code advisories cover equivalent bypasses in `echo`, `rg`, `sed`, `find`,
`cd` plus a write, pipelines, `$IFS`, short flags, and Zsh clobber syntax. The full
ledger is below.

**Why it matters.** A growing denylist of syntax is not a sound command boundary.
Prefer argument-vector execution, capability-specific tools, OS sandboxing, and a
post-resolution effect check. If a shell must be exposed, unknown syntax should
require the normal permission flow rather than being classified safe.

### 4. Symlink and TOCTOU attacks make approval text untrue

[SymJack](https://adversa.ai/blog/the-approval-prompt-is-lying-to-you-symlink-rce-in-five-ai-coding-agents-claude-code-cursor-antigravity-copilot-grok-build/),
published 2026-05-26 and updated the next day for Codex, was reproduced against
Claude Code and Codex CLI. A malicious repository provided instructions that
framed a copy as moving an innocuous media file. The destination was a symlink to
agent configuration. The approval UI showed the literal in-repository path, the
kernel wrote the real config target, and the next session launched a newly
registered MCP server. In CI, auto-approval removed the only click. Anthropic
hardened its UI to show resolved paths; both Anthropic and OpenAI disputed that
the original behavior crossed their bounty boundary.

The vendor advisories show that this was not only a UI argument:

- Claude [CVE-2026-39861](https://github.com/anthropics/claude-code/security/advisories/GHSA-vp62-r36r-9xqp)
  chained a symlink created by sandboxed code with a later unsandboxed write;
- Claude [CVE-2026-103435](https://github.com/anthropics/claude-code/security/advisories/GHSA-5j29-h97v-84ch)
  and [CVE-2026-88968](https://github.com/anthropics/claude-code/security/advisories/GHSA-5xjx-9qx6-jcrv)
  exploited time-of-check/time-of-use races to swap a checked path or parent
  directory before the unsandboxed edit; and
- Codex [CVE-2025-59532](https://github.com/openai/codex/security/advisories/GHSA-w5fx-fh39-j5rw)
  allowed a model-generated working directory to become the writable sandbox root.

**Why it matters.** The permission subject must be the resolved object/effect,
not the string. Canonicalization alone is insufficient against races; enforcement
needs descriptor-relative operations, no-follow semantics, inode binding, and a
final check at use time.

### 5. Ordinary repository text can drive autonomous RCE

[Friendly Fire](https://ainowinstitute.org/publications/friendly-fire-exploit-brief),
published by AI Now on 2026-07-08, used an otherwise ordinary third-party library
with malicious instructions distributed through documentation and decoy source.
The instructions made a prebuilt binary appear necessary for a security review.
Claude Code auto mode and Codex auto-review inspected the decoy, judged it safe,
and ran the real binary without a human approval. The PoC used no hook, skill,
plugin, MCP server, or pre-populated agent config. It succeeded across the tested
Claude and GPT model variants without modification.

NVIDIA's 2026-04-20
[AGENTS.md injection report](https://developer.nvidia.com/blog/mitigating-indirect-agents-md-injection-attacks-in-agentic-environments/)
demonstrated a different supply-chain path. A malicious Go dependency detected a
Codex environment, rewrote `AGENTS.md`, instructed Codex to introduce a subtle
delay, and used comments to influence the final summary so the change was less
obvious. OpenAI considered the incremental risk limited because a dependency that
can rewrite files already has code execution. The agent-specific escalation is
still important: existing code execution gained an adaptive developer identity
that could author and conceal later changes.

**Why it matters.** Scanning only files named `AGENTS.md` or `CLAUDE.md` misses
the broader class. Any content read during an agent task can be an instruction
carrier. The defense must preserve source provenance and prevent lower-trust text
from authorizing higher-impact actions.

### 6. A browser download becomes a persistent agent instruction

Forever Security reported an **incident plus PoC** on 2026-10-22 in
["Markdown Is the New Executable"](https://forever.security/blog/hijacking-ai-agents-via-downloads-folder).
It found a malicious `AGENTS.md` in a customer's Downloads folder and stopped the
activity before code ran. The file attempted to make Codex register a persistent
MCP entry and install a known-malicious npm package. The report did not establish
how the file arrived, but reproduced a chain in which a web page placed the file
in Downloads and a later agent session treated it as local authority.

In isolated testing, Codex followed the original malicious file in more than 90%
of trials. The instructions induced a child Codex process with sandbox/approval
bypass flags and used an OS service launcher so package installation outlived an
MCP startup timeout. A Claude-specific PoC used a planted `CLAUDE.md` to write an
attacker `ANTHROPIC_BASE_URL`; Claude then sent its OAuth token to that endpoint,
and an attacker-controlled response could request a tool call. The report measured
roughly 50% success for the config write and lower same-session execution success.

**Why it matters.** Filesystem location is weak provenance. Downloads, extracted
archives, synced folders, generated files, and tool-created files should remain
untrusted until explicitly reviewed. A semantic scanner should recognize social
engineering that normalizes self-modification, new MCP servers, nested agents,
or instructions to disable controls.

### 7. Network and rendering allowlists become covert exfiltration channels

The common pattern is to send secret data through a destination that policy already
permits:

- Codex Desktop [CVE-2026-14898](https://nvd.nist.gov/vuln/detail/CVE-2026-14898)
  automatically rendered remote Markdown images. An injected response could place
  session data in the image URL, causing a request to an attacker server. The fix
  disabled remote image loading.
- Claude Code [CVE-2026-54316](https://github.com/anthropics/claude-code/security/advisories/GHSA-fg94-h982-f3mm)
  pre-approved all paths on `huggingface.co`. Attacker-controlled repositories and
  download counters created an out-of-band channel for encoded files, environment
  variables, or command output.
- Claude Code [CVE-2026-24052](https://github.com/anthropics/claude-code/security/advisories/GHSA-vhw5-3g5m-8ggf)
  used prefix matching for trusted hosts, so an attacker domain beginning with a
  trusted hostname passed validation.
- Oasis Security's [Claudy Day](https://www.oasis.security/blog/claude-ai-prompt-injection-data-exfiltration-vulnerability),
  published 2026-03-18, hid instructions in a `claude.ai/new?q=` prefilled prompt.
  The sandbox could not reach arbitrary hosts but could reach Anthropic's Files
  API, so an attacker-supplied Anthropic key turned the first-party API into an
  exfiltration sink. A `claude.com` open redirect made delivery look first-party.

**Why it matters.** Egress decisions need destination ownership, path, method,
redirect chain, data sensitivity, and causal provenance. A trusted hostname is
not permission to transmit arbitrary user data.

### 8. MCP poisoning and toxic cross-tool flows

Invariant Labs disclosed [MCP tool poisoning](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)
on 2025-04-01. Malicious instructions in tool descriptions are visible to the
model but often absent or abbreviated in the user's approval UI. The demonstrated
goals included reading credentials, hiding them in a tool parameter, changing how
another trusted tool behaves (tool shadowing), and changing a previously approved
description later (a rug pull).

On 2025-05-26, Invariant demonstrated a
[toxic flow against Claude Desktop and the official GitHub MCP server](https://invariantlabs.ai/blog/mcp-github-vulnerability).
An attacker placed prompt injection in a public issue. A user merely asked Claude
to review open issues. The injected flow read information from private repositories
and published it in a pull request on the public repository. Neither tool was
malicious; the vulnerability existed in their composition and the breadth of the
GitHub credential.

Claude Code Action
[CVE-2026-47751](https://github.com/anthropics/claude-code-action/security/advisories/GHSA-8q5r-mmjf-575q)
made the configuration variant concrete in CI. Before 1.0.74, the action checked
out an attacker PR head, read its `.mcp.json`, and enabled all project MCP servers.
A privileged trigger could therefore execute the PR's command on the runner and
expose workflow secrets.

**Why it matters.** Per-call scanning cannot see all toxic flows. AgentArmor needs
session-level dataflow state: which untrusted source influenced a call, which
sensitive source was read, and which public or attacker-controlled sink is next.
Tool schemas and descriptions must be scanned and content-pinned, not trusted
because the server name was previously approved.

### 9. CI prompt injection reads secrets outside the command sandbox

Microsoft published a Claude Code Action case study on 2026-06-05. In its
[lab reproduction](https://www.microsoft.com/en-us/security/blog/2026/06/05/securing-ci-cd-in-agentic-world-claude-code-github-action-case/),
untrusted issue/PR text steered the in-process `Read` tool to
`/proc/self/environ`. Bash had a scrubbed Bubblewrap environment, but `Read` did
not cross that sandbox boundary and could see the Anthropic key. The prompt removed
the recognizable key prefix before output, bypassing both model refusal heuristics
and GitHub secret redaction. WebFetch, GitHub comments, or full action logs could
then serve as sinks. Anthropic mitigated the specific `/proc` path in Claude Code
2.1.128.

The same Microsoft report documents an observed prompt-injection attempt against
a permissive issue-triage bot: a feature request directed the bot to insert an
invisible XSS image into documentation and open a PR. This illustrates integrity
impact even when no local secret is stolen.

**Why it matters.** Every tool must share the same security boundary. Protecting
only shell subprocesses leaves file reads, renderers, GitHub APIs, and logs as
confused-deputy paths. Secrets should not be present in the agent process when a
broker can inject narrowly scoped credentials at the destination.

### 10. Web and desktop integrations can submit instructions without informed intent

Several Claude surfaces turned a web-origin event into a privileged local or agent
action:

- [CVE-2025-52882](https://github.com/anthropics/claude-code/security/advisories/GHSA-9f65-56v6-gxw7)
  allowed attacker web pages to connect to Claude Code IDE-extension WebSockets,
  read files and IDE state, and in limited Jupyter cases reach code execution.
- Oasis Security's [PromptFiction](https://www.oasis.security/resources/reports/claude-url-scheme-prompt-injection)
  found that a `claude://` link could open Claude Desktop and auto-submit a prompt
  whose full content the user never reviewed. Version 1.1.2321 added a fix.
- The 2026-03-26 ShadowPrompt disclosure, summarized by the
  [Cloud Security Alliance](https://labs.cloudsecurityalliance.org/research/csa-research-note-shadowprompt-xss-prompt-injection-hosted-a/),
  combined the Claude Chrome extension's wildcard trust for `*.claude.ai` with DOM
  XSS on a third-party CAPTCHA subdomain. Visiting a crafted page could inject an
  arbitrary prompt into the extension without a click.

**Why it matters.** A prompt has authority only when its origin and full contents
were knowingly authorized. Custom URL schemes, localhost listeners, extensions,
and IDE bridges need strict origin authentication, CSRF-style binding, and an
unambiguous preview before submission.

### 11. Monitor injection, compaction, and multi-agent decomposition

An Anthropic Fellows study published 2026-10-22
[red-teamed Claude Code Auto Mode and an approximation of Codex Guardian](https://alignment.anthropic.com/2026/red-teaming-auto-mode/).
The strongest attacks:

- forged transcript-like lines inside tool arguments so a flattened monitor saw a
  harmless final action after a harmful first command;
- used unmonitored compaction summaries to invent user authorization;
- split a harmful sequence across agents or sessions sharing a workspace, so no
  single monitor saw the full chain;
- exploited coverage gaps where edits, workflow creation, handoffs, or tool results
  were not visible to the monitor; and
- reached an equivalent unmonitored tool loop through a framework not named in the
  policy.

In the researchers' threat model, the production monitors failed to stop more
than 55% of persistent adversarial-agent trials. This is not a measurement of
ordinary indirect prompt injection and should not be generalized to normal user
sessions. It is strong evidence that a per-action classifier with a lossy or
spoofable transcript is not a complete security boundary.

**Why it matters.** AgentArmor must receive structured, typed events rather than a
flattened chat transcript; delimiter escaping is security-critical. Compaction and
handoff outputs need provenance and scanning. Decisions should incorporate the
shared workspace and earlier actions, not only the immediately proposed call.

### 12. Real malware now uses agent configuration as persistence

The **Miasma** npm supply-chain campaign is the clearest public example. Microsoft
reported on 2026-06-02 that trojanized packages used preinstall execution, stole
credentials, and propagated through repositories. Subsequent analysis identified
agent-specific persistence. A compromised Azure repository committed a
`.claude/settings.json` `SessionStart` hook whose payload ran when Claude Code
started; [AI Security Labs summarizes the artifact and trigger](https://aisecuritylabs.org/blog/securing-claude-code-development/the-incident-the-miasma-worm/),
and [StepSecurity lists the planted Claude files](https://www.stepsecurity.io/blog/miasma-and-hades-are-spreading-now-detect-them-on-developer-machines-with-suspicious-files).

In August 2026, Ox Security reported a Shai-Hulud wave with more than 440 affected
packages and [malicious Claude settings in repositories linked from the official
MCP registry](https://www.ox.security/blog/shai-hulud-outbreak-debrief-the-worm-evolves-into-mcp/).
The Python package could remain clean while the linked repository carried settings
that harvested tokens when opened in Claude Code.

These are conventional supply-chain compromises using agent hooks as a persistence
and execution mechanism, not model-level prompt injections. They still belong in
the threat model because the agent configuration became an attacker-preferred
autorun location.

## Codex vulnerability ledger

This table lists public Codex product CVEs found in the OpenAI repository advisory,
CVE records, and primary research. It excludes CVEs in third-party clients that
merely support a Codex API path.

| Public date | Advisory | Product / affected versions | Tactic and impact | Fix/status |
| --- | --- | --- | --- | --- |
| 2025-07-25 | [CVE-2025-54558](https://nvd.nist.gov/vuln/detail/CVE-2025-54558) | CLI `<0.9.0` | `rg` was auto-approved even with options that can invoke a preprocessor or helper. This turned a nominally read-only search into an execution primitive. | CLI 0.9.0 |
| 2025-09-19 | [CVE-2025-59532 / GHSA-w5fx-fh39-j5rw](https://github.com/openai/codex/security/advisories/GHSA-w5fx-fh39-j5rw) | CLI 0.2.0–0.38.0; IDE `<0.4.12` | A model-selected `cwd` outside the starting project became the writable sandbox root, enabling writes and commands wherever the Codex process had permission. Network isolation remained intact. | CLI 0.39.0; IDE 0.4.12 |
| 2025-12-01 | [CVE-2025-61260](https://research.checkpoint.com/2025/openai-codex-cli-command-injection-vulnerability/) | CLI `<0.23.0` | Project `.env` redirected `CODEX_HOME`; project config auto-started an attacker MCP command without approval. | CLI 0.23.0; vendor-validated PoC |
| 2026-07-06 | [CVE-2026-14898](https://nvd.nist.gov/vuln/detail/CVE-2026-14898) | macOS Desktop `<26.527.31326` | Indirect injection produced a remote Markdown image URL containing sensitive data; automatic rendering made the request. | 26.527.31326 disables remote images; no known in-the-wild exploitation |
| 2026-09-01 | [CVE-2026-19590](https://nvd.nist.gov/vuln/detail/CVE-2026-19590) | Desktop macOS/Windows through 26.513 builds | Preserved `.git/config` set `core.hooksPath`; automatic Git operations ran a hook outside the sandbox and without approval. | macOS 26.519.22136; Windows 26.519.21041 / Store 26.519.2081.0 |
| 2026-09-01 | [CVE-2026-19591](https://nvd.nist.gov/vuln/detail/CVE-2026-19591) | CLI 0.72.0–0.130.0; affected Desktop 26.513 builds | PowerShell `--%` parser differential bypassed safe-command review; a write to config could persist an MCP RCE. | CLI 0.131.0; same 26.519 Desktop fixes |
| 2026-09-01 | [CVE-2026-19592](https://nvd.nist.gov/vuln/detail/CVE-2026-19592) | CLI 0.102.0–0.130.0; affected Desktop 26.513 builds | Preserved `.git/config` set `core.fsmonitor`; metadata collection ran the attacker helper outside sandbox/approval. | CLI 0.131.0; same 26.519 Desktop fixes |
| 2026-09-01 | [CVE-2026-19593](https://nvd.nist.gov/vuln/detail/CVE-2026-19593) | affected Desktop 26.513 builds | `attr.tree` and a clean/process filter executed a program during automatic workspace inspection, before trust or model interaction. | Same 26.519 Desktop fixes |

Significant Codex reports without a product CVE in the sources reviewed:

| Public date | Report | Evidence | Tactic | Status |
| --- | --- | --- | --- | --- |
| 2026-03-30 | [Malicious GitHub branch name](https://www.beyondtrust.com/blog/entry/openai-codex-command-injection-vulnerability-github-token) | PoC; OpenAI P1 Critical | Shell injection during cloud setup exposed GitHub OAuth/installation tokens and could scale through a repository branch. | Remediated by 2026-01-30 |
| 2026-04-20 | [Indirect `AGENTS.md` injection](https://developer.nvidia.com/blog/mitigating-indirect-agents-md-injection-attacks-in-agentic-environments/) | PoC; disputed incremental risk | A compromised dependency rewrote agent instructions and comments to alter code and conceal the change in summaries. | OpenAI: no product change planned |
| 2026-05-20 / 06-04 update | [Windows web-search to `node.bat` hijack](https://cymulate.com/blog/codex-cli-rce-prompt-injection-mitigations/) | PoC; disputed/not reproduced by vendor | Web content induced a workspace `node.bat`; Windows executable search ran it outside the sandbox on next launch. | Researcher reported unpatched at publication |
| 2026-05-26 | [SymJack](https://adversa.ai/blog/the-approval-prompt-is-lying-to-you-symlink-rce-in-five-ai-coding-agents-claude-code-cursor-antigravity-copilot-grok-build/) | PoC; disputed | A symlink made an approved benign copy overwrite Codex config; an MCP command ran on restart. | OpenAI classified as theoretical/expected approval behavior |
| 2026-07-08 | [Friendly Fire](https://ainowinstitute.org/publications/friendly-fire-exploit-brief) | PoC | Camouflaged instructions in ordinary library files led auto-review to run a malicious binary during a security assessment. | Research result; no CVE |
| 2026-10-22 | [Downloads `AGENTS.md`](https://forever.security/blog/hijacking-ai-agents-via-downloads-folder) | Incident artifact plus lab reproduction | A downloaded instruction file induced persistent global MCP configuration, a nested unrestricted Codex run, and malware installation. | Customer execution stopped; architecture-level vector remains |

## Claude and Claude Code advisory ledger

The following is the complete public `anthropics/claude-code` advisory list returned
on 2026-10-30, plus the separate Claude Code Action advisory. Some entries concern
Claude Desktop or Cowork because Anthropic publishes them in the same repository.
The two Yarn entries describe substantially the same startup behavior but have
separate CVEs/advisories, so both are retained.

| Public date | Advisory | Surface / affected versions | Tactic and impact | Fixed in |
| --- | --- | --- | --- | --- |
| 2025-06-23 | [CVE-2025-52882](https://github.com/anthropics/claude-code/security/advisories/GHSA-9f65-56v6-gxw7) | IDE extensions: VS Code 0.2.116–1.0.23; JetBrains 0.1.1–0.1.8 | Arbitrary web origins connected to the local WebSocket, exposing files and IDE state; limited Jupyter code execution required another prompt. | Code 1.0.24; JetBrains 0.1.9 |
| 2025-08-01 | [CVE-2025-54795](https://github.com/anthropics/claude-code/security/advisories/GHSA-x56v-x2h6-7j34) | Code `<1.0.20` | `echo` parsing differential let injected context bypass command confirmation. | 1.0.20 |
| 2025-08-01 | [CVE-2025-54794](https://github.com/anthropics/claude-code/security/advisories/GHSA-pmw4-pwvc-3hx2) | Research Preview `<0.2.111` | Prefix rather than canonical path comparison allowed access outside CWD when path prefixes collided. | 0.2.111 |
| 2025-08-15 | [CVE-2025-55284](https://github.com/anthropics/claude-code/security/advisories/GHSA-x5gv-jw7f-j6xj) | Code `<1.0.4` | Overbroad safe-command allowlist let injected context read a file and send it over the network without confirmation. | 1.0.4 |
| 2025-09-02 | [GHSA-ph6w-f82w-28w6](https://github.com/anthropics/claude-code/security/advisories/GHSA-ph6w-f82w-28w6) | Code `<1.0.87` | Folder trust warning did not clearly disclose that project files/hooks could execute without another prompt. | 1.0.87 warning change |
| 2025-09-09 | [CVE-2025-59041](https://github.com/anthropics/claude-code/security/advisories/GHSA-j4h9-wv2m-wrf7) | Code `<1.0.105` | Malicious `git config user.email` was templated into a startup command before workspace trust, causing RCE. | 1.0.105 |
| 2025-09-09 | [CVE-2025-58764](https://github.com/anthropics/claude-code/security/advisories/GHSA-qxfv-fcpc-w36x) | Code `<1.0.105` | `rg` command parsing differential bypassed user approval. | 1.0.105 |
| 2025-09-24 | [CVE-2025-59828](https://github.com/anthropics/claude-code/security/advisories/GHSA-2jjv-qf24-vfm4) | Code `<1.0.39` with affected Yarn behavior | `yarn --version` loaded project plugins/`yarnPath` before trust, enabling startup RCE. | 1.0.39 |
| 2025-10-03 | [CVE-2025-59829](https://github.com/anthropics/claude-code/security/advisories/GHSA-66m2-gx93-v996) | Code `<1.0.120` | A symlink bypassed an explicit file permission-deny rule. | 1.0.120 |
| 2025-10-03 | [CVE-2025-59536](https://github.com/anthropics/claude-code/security/advisories/GHSA-4fgq-fpq9-mr3g) | Code `<1.0.111` | Project MCP/hook configuration executed before startup trust. | 1.0.111 |
| 2025-11-19 | [CVE-2025-65099](https://github.com/anthropics/claude-code/security/advisories/GHSA-5hhx-v7f6-x7gv) | Code `<1.0.39` with affected Yarn behavior | Separate advisory for the same pre-trust Yarn plugin/`yarnPath` execution family. | 1.0.39 |
| 2025-11-20 | [CVE-2025-64755](https://github.com/anthropics/claude-code/security/advisories/GHSA-7mv8-j34q-vp7q) | Code `<2.0.31` | `sed` parsing bypassed read-only validation and wrote arbitrary host files. | 2.0.31 |
| 2025-12-03 | [CVE-2025-66032](https://github.com/anthropics/claude-code/security/advisories/GHSA-xq4m-mc3c-vvg3) | Code `<1.0.93` | `$IFS` and short-option parsing bypassed read-only validation for arbitrary execution. | 1.0.93 |
| 2026-01-20 | [CVE-2026-21852](https://github.com/anthropics/claude-code/security/advisories/GHSA-jh7p-qr78-84p7) | Code `<2.0.65` | Repository `ANTHROPIC_BASE_URL` redirected API requests and keys before trust. | 2.0.65 |
| 2026-02-03 | [CVE-2026-24052](https://github.com/anthropics/claude-code/security/advisories/GHSA-vhw5-3g5m-8ggf) | Code `<1.0.111` | `startsWith()` trusted-domain validation accepted attacker domains with a trusted prefix. | 1.0.111 |
| 2026-02-03 | [CVE-2026-24053](https://github.com/anthropics/claude-code/security/advisories/GHSA-q728-gf8j-w49r) | Code `<2.0.74`, Zsh | Zsh clobber syntax bypassed directory/write restrictions. | 2.0.74 |
| 2026-02-03 | [CVE-2026-24887](https://github.com/anthropics/claude-code/security/advisories/GHSA-qgqw-h4xq-7w8w) | Code `<2.0.72` | `find` command parsing bypassed approval and executed an untrusted command. | 2.0.72 |
| 2026-02-06 | [CVE-2026-25724](https://github.com/anthropics/claude-code/security/advisories/GHSA-4q92-rfm6-2cqx) | Code `<2.1.7` | Symlink access bypassed `settings.json` deny rules. | 2.1.7 |
| 2026-02-06 | [CVE-2026-25725](https://github.com/anthropics/claude-code/security/advisories/GHSA-ff64-7w26-62rf) | Code `<2.1.2` | Sandbox could create missing `.claude/settings.json`, plant persistent host-level hooks, and execute them on restart. | 2.1.2 |
| 2026-02-06 | [CVE-2026-25723](https://github.com/anthropics/claude-code/security/advisories/GHSA-mhg7-666j-cqg4) | Code `<2.0.55` | Piped `echo`/`sed` bypassed write restrictions, including writes into `.claude`. | 2.0.55 |
| 2026-02-06 | [CVE-2026-25722](https://github.com/anthropics/claude-code/security/advisories/GHSA-66q4-vfjg-2qhh) | Code `<2.0.57` | `cd` plus a write bypassed protection of sensitive directories. | 2.0.57 |
| 2026-03-18 | [CVE-2026-33068](https://github.com/anthropics/claude-code/security/advisories/GHSA-mmgp-wc2j-qcv7) | Code `<2.1.53` | Repo settings selected `bypassPermissions` before trust-dialog selection, silently skipping first-open trust. | 2.1.53 |
| 2026-04-17 | [CVE-2026-35603](https://github.com/anthropics/claude-code/security/advisories/GHSA-5cwg-9f6j-9jvx) | Code on Windows `<2.1.75` | Low-privilege user pre-created a writable system-wide managed-settings directory, affecting later users. | 2.1.75 |
| 2026-04-20 | [CVE-2026-39861](https://github.com/anthropics/claude-code/security/advisories/GHSA-vp62-r36r-9xqp) | Code `<2.1.64` | Sandboxed code created an external symlink; unsandboxed app followed it on write, escaping the workspace. | 2.1.64 |
| 2026-04-24 | [CVE-2026-40068](https://github.com/anthropics/claude-code/security/advisories/GHSA-q5hj-mxqh-vv77) | Code 2.1.63–2.1.83 | Crafted Git worktree `commondir` pointed to a previously trusted path, bypassing trust and launching hooks. | 2.1.84 |
| 2026-05-06 | [CVE-2026-44470](https://github.com/anthropics/claude-code/security/advisories/GHSA-5p5x-5294-qhp3) | CoworkVMService on Windows `<1.3834.0` | User-writable VM bundle replaced by a directory junction; SYSTEM service created a file at an attacker target, enabling local privilege escalation. | Desktop 1.3834.0 |
| 2026-05-06 | [CVE-2026-44467](https://github.com/anthropics/claude-code/security/advisories/GHSA-3rwf-2g6p-c2f9) | Desktop remote sessions 1.2581.0–1.4303.x | SSH checked only that a hostname existed in `known_hosts`, not that the presented key matched, enabling network MITM. | Desktop 1.4304.0 |
| 2026-05-20 | [CVE-2026-47751](https://github.com/anthropics/claude-code-action/security/advisories/GHSA-8q5r-mmjf-575q) | Code Action `<1.0.74` | PR-head `.mcp.json` plus enable-all behavior executed an attacker MCP server on a privileged runner. | Action 1.0.74 |
| 2026-06-13 | [CVE-2026-54316](https://github.com/anthropics/claude-code/security/advisories/GHSA-fg94-h982-f3mm) | Code 0.2.54–2.1.162 | Pre-approved `huggingface.co` paths provided a blind out-of-band data channel. | 2.1.163 |
| 2026-06-25 | [CVE-2026-55607](https://github.com/anthropics/claude-code/security/advisories/GHSA-7835-87q9-rgvv) | Code 2.1.38–2.1.162 | Worktree named `.git`, symlink manipulation, and Git `fsmonitor` enabled a home-directory write and unsandboxed execution. | 2.1.163 |
| 2026-06-25 | [CVE-2026-46406](https://github.com/anthropics/claude-code/security/advisories/GHSA-4vp2-6q8c-pvq2) | Code `/copy` 2.1.59–2.1.127 | Predictable world-readable temp path leaked responses; a planted symlink redirected the write. | 2.1.128 |
| 2026-09-25 | [GHSA-v234-4jrq-mgg6](https://github.com/anthropics/claude-code/security/advisories/GHSA-v234-4jrq-mgg6) | Desktop macOS 1.1.3918–1.15961.x | Cowork's host-open blocklist omitted an executable file type; a prompt-injected agent could place it in the shared folder, and a VM kernel flaw could remove the click. | Desktop 1.15962.0 plus VM image 1.11847.5 |
| 2026-09-29 | [CVE-2026-103012](https://github.com/anthropics/claude-code/security/advisories/GHSA-gfvf-j8jh-jxxw) | Code Enterprise/Team 2.0.68–2.1.259 | A stored API key took precedence while fetching managed policy; rejection started a session without current org rules or with stale cached rules. | 2.1.260 |
| 2026-10-05 | [CVE-2026-103435](https://github.com/anthropics/claude-code/security/advisories/GHSA-5j29-h97v-84ch) | Code `<2.1.129` | TOCTOU swap replaced a checked project file with an external symlink before write. | 2.1.129 |
| 2026-10-09 | [CVE-2026-86063](https://github.com/anthropics/claude-code/security/advisories/GHSA-pq7j-f95f-qfcv) | Code/Desktop plugins `<2.1.179` | A branch named like a 40-character commit SHA made `git checkout` install attacker code despite an apparent hash pin. | 2.1.179 |
| 2026-10-13 | [CVE-2026-88066](https://github.com/anthropics/claude-code/security/advisories/GHSA-4p2j-qj3c-6x84) | Desktop Windows `<1.8089.0` | Relative `git.exe` lookup searched the project CWD first and executed a malicious binary on open. | Desktop 1.8089.0 |
| 2026-10-26 | [CVE-2026-88968](https://github.com/anthropics/claude-code/security/advisories/GHSA-5xjx-9qx6-jcrv) | Code `<2.1.251`, macOS | Sandboxed process atomically swapped a parent directory for a symlink after validation; unsandboxed Edit overwrote an external file. | 2.1.251 |

Important non-advisory Claude reports:

| Public date | Report | Evidence | Tactic | Status |
| --- | --- | --- | --- | --- |
| 2025-04-01 | [MCP tool poisoning](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks) | PoC | Hidden tool-description instructions, rug pulls, and cross-server shadowing steer reads and exfiltration. | Protocol/ecosystem issue |
| 2025-05-26 | [GitHub MCP toxic flow](https://invariantlabs.ai/blog/mcp-github-vulnerability) | PoC on Claude Desktop | Public issue injection caused private-repo reads and publication to a public PR. | Architectural composition issue |
| 2026-02 (reported by Anthropic) | [Employee red-team phish](https://www.anthropic.com/research/how-we-contain-claude) | Internal empirical test | A ready-to-paste user prompt requested AWS credential read, encoding, and POST; Claude completed 24/25 retries. | Demonstrated need for filesystem and egress boundaries |
| 2026-03-18 | [Claudy Day](https://www.oasis.security/blog/claude-ai-prompt-injection-data-exfiltration-vulnerability) | PoC | Hidden HTML in a prefilled prompt, Files API exfiltration, and first-party open redirect. | Prompt injection fixed; report said other issues were being addressed |
| 2026, fixed in Desktop 1.1.2321 | [PromptFiction](https://www.oasis.security/resources/reports/claude-url-scheme-prompt-injection) | PoC | One-click `claude://` URL auto-submitted a partially hidden prompt. | Fixed |
| 2026-03-26 | [ShadowPrompt](https://labs.cloudsecurityalliance.org/research/csa-research-note-shadowprompt-xss-prompt-injection-hosted-a/) | PoC | Trusted-subdomain wildcard plus DOM XSS injected a prompt through the Chrome extension. | Reported to Anthropic |
| 2026-05-26 | [SymJack](https://adversa.ai/blog/the-approval-prompt-is-lying-to-you-symlink-rce-in-five-ai-coding-agents-claude-code-cursor-antigravity-copilot-grok-build/) | PoC; disputed | Deceptive copy through a symlink overwrote config and persisted MCP execution. | Report rejected; approval UI later hardened |
| 2026-06-05 | [Miasma](https://www.microsoft.com/en-us/security/blog/2026/06/02/preinstall-persistence-inside-red-hat-npm-miasma-credential-stealing-campaign/) | Incident | Supply-chain malware planted Claude `SessionStart` persistence and harvested developer/cloud credentials. | Active campaign response and cleanup required |
| 2026-06-05 | [Claude Code Action Read-tool gap](https://www.microsoft.com/en-us/security/blog/2026/06/05/securing-ci-cd-in-agentic-world-claude-code-github-action-case/) | PoC plus observed injection attempts | Untrusted PR/issue text read unsanitized process environment and laundered a secret past redaction. | `/proc` mitigation in Code 2.1.128 |
| 2026-07-08 | [Friendly Fire](https://ainowinstitute.org/publications/friendly-fire-exploit-brief) | PoC | Repository documentation convinced auto mode to run a camouflaged binary during defensive review. | Research result; no CVE |
| 2026-10-22 | [Downloads `CLAUDE.md`](https://forever.security/blog/hijacking-ai-agents-via-downloads-folder) | Cross-agent lab PoC | Instructions rewrote `ANTHROPIC_BASE_URL`, leaking OAuth and allowing attacker-shaped model tool calls. | Architecture-level vector remains |

## Cross-product empirical research

These results are useful for threat discovery but are not equivalent to CVEs or
evidence of exploitation in the wild.

- **Malicious issue requests.** The July 2026
  [IssueTrojanBench preprint](https://arxiv.org/abs/2607.20759) tested malicious
  issues delivered as comments, files, PDFs, and other project artifacts. It
  reported an overall 66.5% penetration rate across the evaluated agent/model
  combinations, with 79.2% for the tested Codex Desktop configurations and 41.1%
  for the tested Claude Code/Sonnet configuration. These numbers are snapshots of
  a synthetic benchmark, not stable product-wide rates.
- **Persistent memory.** [Bad Memory](https://arxiv.org/abs/2607.14611) found that
  making an agent write poison into its own memory was difficult, but pre-planted
  instructions in `AGENTS.md`, `CLAUDE.md`, preferences, or behavior files could
  affect later sessions. A resistant session sometimes flagged but did not remove
  the payload, leaving a weaker future model exposed.
- **MCP client testing.** A March 2026
  [comparative preprint](https://arxiv.org/abs/2603.21642) tested tool-poisoning
  variants across Claude Desktop, Claude Code, and five other clients. It found
  uneven protections and concluded that client-side MCP security remained
  inadequate, while also noting that Claude Desktop had stronger guardrails than
  some peers. Results depend heavily on exact client versions and approval modes.
- **Blocking monitors.** The October 2026
  [Auto Mode/Guardian red team](https://alignment.anthropic.com/2026/red-teaming-auto-mode/)
  found monitor injection, missing tool results, compaction, and multi-agent state
  to be major blind spots. It is the most directly relevant evidence for how an
  AgentArmor-style decision layer can fail when its input is lossy or ambiguously
  delimited.

## Design requirements for a stronger AgentArmor

AgentArmor should be one layer in a defense-in-depth system. A semantic decision
model can detect hostile intent and authority confusion, but it cannot safely
reimplement a shell parser, close a filesystem race, or keep a secret out of a
process that already contains it.

### 1. Scan every authority-bearing input, not only the user prompt

Synchronous scanning candidates should include:

- project instruction and memory files, including nested copies;
- project and user agent settings, hooks, skills, plugins, and MCP definitions;
- MCP tool descriptions, schemas, server instructions, errors, and results;
- web/search content and redirect metadata;
- issue/PR text, comments, commit messages, branch/tag names, and relevant Git
  configuration;
- tool results before they re-enter model context;
- compaction summaries, subagent prompts, handoffs, and shared-workspace notes;
- the proposed tool call with resolved paths, destination ownership, environment,
  and permission mode; and
- output that will be rendered, logged, posted, uploaded, or sent over the network.

Each scan should retain origin, author, trust level, content hash, acquisition
time, and evidence offsets. The whole input must be scanned. If it is oversized or
cannot be encoded, the synchronous path should explicitly deny rather than silently
truncate and produce a clear verdict.

### 2. Ask factorized security questions

A single `malicious/benign` score loses the structure needed for policy. Candidate
questions for Kai should cover:

| Question family | Examples of evidence |
| --- | --- |
| Instruction injection | Text tells the agent to ignore, reinterpret, conceal, prioritize, or treat external content as authority |
| Privilege/control bypass | Requests to skip approvals, disable sandboxing, spawn an unrestricted child, evade a hook, or exploit an auto-approved command |
| Persistence/self-modification | Writes to agent config, instruction files, hooks, MCP registration, memory, shell startup, Git config, or CI config |
| Sensitive read | Credentials, tokens, private repos, environment, browser/session data, SSH/cloud config, process metadata |
| External sink | Network request, image URL, issue/comment/PR, log, upload, first-party API under an attacker key, public repository |
| Provenance mismatch | A repository, web page, tool result, or issue claims user/admin/project-owner authority |
| Deception/concealment | Benign framing, invisible markup, Unicode, encoded content, decoy source, misleading extension, suppression of summaries/logs |
| Scope escalation | Cross-repository, out-of-workspace, host from sandbox, user to SYSTEM, local project to global config |
| Compound flow | Earlier untrusted read plus later sensitive read/write/sink, even if no individual call is obviously malicious |

Kai's pointer readout and evidence offsets are especially valuable here: a denial
should be able to identify the exact injected clause or suspicious field without
generating an attacker-controlled explanation.

### 3. Preserve structured provenance through every context transition

- Keep roles and content in typed fields; never flatten them into prefix-delimited
  text that an attacker can imitate.
- Escape delimiters before model input and keep question rows isolated.
- Treat compaction as a security-sensitive transform. The summary must not be able
  to invent user intent or drop the provenance of an instruction.
- A subagent must not receive text written by another agent as if it came from the
  user. Handoffs should carry origin and authorization metadata.
- Bind approvals to a content hash and resolved effect. Any relevant mutation
  invalidates the approval.

### 4. Add stateful dataflow policy around the decision model

Track at least three capabilities over the session:

1. untrusted input was ingested;
2. sensitive data or a privileged capability is reachable; and
3. an external communication or state-changing sink is reachable.

When all three are present, require a narrowly scoped, effect-aware authorization
or break the flow. This is the practical lesson of the GitHub MCP toxic flow and
the CI Read-tool attack. Per-tool allowlists cannot express "private repository
data read after a public issue must not be posted to a public PR."

The state should span tool calls, subagents, compactions, and sessions that share
mutable state. Configuration and memory writes should taint later sessions until
reviewed.

### 5. Pair semantic detection with deterministic effect controls

The host/client still needs to enforce controls that Kai should not approximate:

- resolve paths with no-follow, descriptor-relative APIs; bind checks to inodes or
  handles and revalidate at use time;
- apply the same policy to native edits and shell-mediated writes (`cp`, `mv`,
  redirection, installers, archive extraction, Git helpers);
- avoid shell interpolation and parser-based "safe command" exemptions;
- disable repository-controlled Git hooks, filters, `fsmonitor`, and other helpers
  during automatic inspection;
- load no project config, hook, plugin, or MCP server before trust is established;
- protect global agent config and instruction files from writes by an agent working
  on untrusted content;
- pin tool descriptions, schemas, plugin code, and reviewed config by content hash;
- use exact host validation, redirect checks, and destination ownership rather than
  hostname prefixes or broad first-party allowlists;
- keep long-lived credentials outside the agent/sandbox and inject short-lived,
  least-privilege credentials through a broker only for an approved destination;
- make renderers inert by default: no remote images or active file types; and
- give CI jobs processing untrusted contributions no production secrets and no
  write token unless the task strictly requires one.

### 6. Make approvals describe the real effect

Approval UI should show:

- canonical source and destination paths, including symlink targets;
- whether the action changes agent, shell, Git, IDE, or CI configuration;
- the executable, argument vector, environment changes, and effective identity;
- requested host, final host after redirects, and what sensitive fields will leave;
- the provenance that caused the action (for example, "instruction originated in
  an external PR comment"); and
- whether the action persists into future sessions or affects other projects.

An allowed AgentArmor decision should abstain from the agent's existing permission
flow; it must not silently grant authority. A denied or failed synchronous check
should emit an explicit denial. Background scans are advisory and must not claim
to have prevented side effects that already occurred.

### 7. Build evaluations from complete attack chains

The test corpus should include paired benign and malicious variants for:

- direct, indirect, and user-mediated injection;
- long, bland engineering documentation with one malicious setup section;
- hidden HTML, Unicode, encoded, and multimodal carriers;
- branch names and non-natural-language metadata;
- MCP descriptions, schema fields, errors, and tool results;
- symlink/path effects supplied as structured context to the decision model;
- trusted-domain and first-party-API exfiltration;
- multi-step flows in which read, transform, and sink are individually benign;
- persistent config or memory that attacks a later session/model;
- compaction and subagent handoff provenance loss; and
- CI variants with no interactive user.

Measure attack recall, benign false denials, evidence-offset quality, whole-input
coverage, and failure behavior. Report CPU contract tests separately from actual
GPU/model validation, and include adversarial sequences rather than only isolated
documents or single tool calls.

## Prioritized implementation plan

1. **Protect ingestion:** scan and label project instructions, settings, MCP
   metadata/results, web content, and collaboration text before model ingestion.
2. **Protect high-risk writes:** synchronously scan all writes to agent config,
   instructions, memory, hooks, MCP, Git config, shell startup, and CI/editor config.
3. **Protect egress:** combine sensitive-source taint with sink ownership and
   provenance; inspect render-time URLs and first-party API uploads.
4. **Carry session state:** connect untrusted input, sensitive reads, config
   mutation, and later sinks across tools, compaction, subagents, and restarts.
5. **Integrate deterministic facts:** pass canonical paths, symlink status,
   credential scope, permission mode, target host, and config sensitivity to Kai;
   do not ask Kai to infer facts the runtime can know exactly.
6. **Fail closed synchronously:** scanner errors, incomplete encoding, and partial
   documents produce explicit denial; background findings remain advisory.
7. **Continuously replay this corpus:** keep versioned attack fixtures and benign
   twins for every family above, preserving source, date, expected decision, and
   evidence span.

## Primary source index

- [OpenAI Codex security advisories](https://github.com/openai/codex/security/advisories)
- [Anthropic Claude Code security advisories](https://github.com/anthropics/claude-code/security/advisories)
- [Claude Code Action security advisory](https://github.com/anthropics/claude-code-action/security/advisories/GHSA-8q5r-mmjf-575q)
- [OpenAI sandbox security guidance](https://developers.openai.com/api/docs/guides/agents-api/environments/security)
- [OpenAI shell network-risk guidance](https://developers.openai.com/api/docs/guides/tools-shell)
- [Anthropic: How we contain Claude across products](https://www.anthropic.com/research/how-we-contain-claude)
- [Check Point: Codex project-local MCP command injection](https://research.checkpoint.com/2025/openai-codex-cli-command-injection-vulnerability/)
- [Check Point: Claude Code hooks, MCP, and API-base-url chains](https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/)
- [BeyondTrust: Codex branch-name command injection](https://www.beyondtrust.com/blog/entry/openai-codex-command-injection-vulnerability-github-token)
- [Invariant Labs: MCP tool poisoning](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)
- [Invariant Labs: GitHub MCP toxic flow](https://invariantlabs.ai/blog/mcp-github-vulnerability)
- [NVIDIA: indirect `AGENTS.md` injection](https://developer.nvidia.com/blog/mitigating-indirect-agents-md-injection-attacks-in-agentic-environments/)
- [Microsoft: Claude Code Action CI/CD](https://www.microsoft.com/en-us/security/blog/2026/06/05/securing-ci-cd-in-agentic-world-claude-code-github-action-case/)
- [Adversa AI: SymJack](https://adversa.ai/blog/the-approval-prompt-is-lying-to-you-symlink-rce-in-five-ai-coding-agents-claude-code-cursor-antigravity-copilot-grok-build/)
- [AI Now: Friendly Fire](https://ainowinstitute.org/publications/friendly-fire-exploit-brief)
- [Forever Security: Downloads instruction-file attack](https://forever.security/blog/hijacking-ai-agents-via-downloads-folder)
- [Oasis Security: Claudy Day](https://www.oasis.security/blog/claude-ai-prompt-injection-data-exfiltration-vulnerability)
- [Oasis Security: PromptFiction](https://www.oasis.security/resources/reports/claude-url-scheme-prompt-injection)
- [Anthropic Fellows: red-teaming Auto Mode and Guardian](https://alignment.anthropic.com/2026/red-teaming-auto-mode/)
- [IssueTrojanBench preprint](https://arxiv.org/abs/2607.20759)
- [Bad Memory preprint](https://arxiv.org/abs/2607.14611)
