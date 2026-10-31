# Codex integration

The templates invoke the native executable directly:

```sh
"/absolute/path/to/agentarmor" hook --agent codex_code
```

No Python interpreter, checkout, environment file, or separate Kai process is
needed. Build/install the executable as described in the [root guide](../../README.md).
Setup follows the [Codex hook contract](https://learn.chatgpt.com/docs/hooks).

**Current status:** native text policy evaluation is unqualified. Every
`PreToolUse` check returns an explicit denial and exit code 2. The observation
hooks report incomplete coverage; they do not scan content or queue work yet.
These templates are for integration development and will block normal tool use.
`agentarmor doctor` reports `policy_ready: false`. They must not be presented as
a working policy-based allow/deny installation.

1. Find the installed executable's absolute path with `command -v agentarmor`.
   Replace `/ABSOLUTE/PATH/agentarmor` in the chosen template with that trusted
   path. Keep its quotes so paths containing spaces work.
2. Merge [config.toml.example](config.toml.example) into a trusted
   `.codex/config.toml` or `~/.codex/config.toml`, or install
   [hooks.json.example](hooks.json.example) as `.codex/hooks.json` or
   `~/.codex/hooks.json`. Install one representation so hooks run once.
3. Restart Codex and use `/hooks` to review and trust the exact commands.
   Prefer user-level or managed configuration outside an untrusted repository.

Check the binary directly before enabling the template:

```sh
printf '%s\n' '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"pwd"}}' \
  | /absolute/path/to/agentarmor hook --agent codex_code
```

Expect `hookSpecificOutput.permissionDecision: deny` and exit code 2, because
the native model frontend is not ready. A future qualified allow must emit no
permission decision and preserve Codex's existing permission flow.

`PostToolUse` and `UserPromptSubmit` are asynchronous; `Stop` is synchronous to
permit a future durable handoff before exit. Currently all three return only an
incomplete-observation message. Post-effect scans remain advisory even after
implementation. Local command hooks require supported local orchestration and
do not cover cloud or hosted tools where the hook is not invoked. See the
[coverage and implementation plan](../../docs/AgentArmor.md).
