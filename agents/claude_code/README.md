# Claude Code integration

[settings.json.example](settings.json.example) invokes the native executable:

```sh
"/absolute/path/to/agentarmor" hook --agent claude_code
```

No Python interpreter, checkout, environment file, or separate Kai process is
needed. Build/install the executable as described in the [root guide](../../README.md).
See the [Claude Code hook contract](https://code.claude.com/docs/en/hooks).

**Current status:** native text policy evaluation is unqualified. Every
`PreToolUse` check returns an explicit denial and exit code 2. Observation hooks
report incomplete coverage; they do not scan files, tool output, or logs yet.
These templates are for integration development and will block normal tool use.
`agentarmor doctor` reports `policy_ready: false`.

1. Find the installed executable's absolute path with `command -v agentarmor`.
   Replace `/ABSOLUTE/PATH/agentarmor` in the template with that trusted path.
   Keep its quotes so paths containing spaces work.
2. Merge the `hooks` entries into your user settings at
   `~/.claude/settings.json` or a reviewed `.claude/settings.json`. Preserve
   existing settings and avoid duplicate entries. Prefer user-level or managed
   settings outside an untrusted repository.
3. Restart Claude Code and inspect the configured commands with `/hooks`.

Check the binary directly before enabling the template:

```sh
printf '%s\n' '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"pwd"}}' \
  | /absolute/path/to/agentarmor hook --agent claude_code
```

Expect `hookSpecificOutput.permissionDecision: deny` and exit code 2. A future
qualified allow must emit no permission decision and preserve Claude Code's
existing permission flow. The CLI also handles `PermissionRequest` denials,
but expanding template coverage requires client-version qualification.

`PostToolUse` and `UserPromptSubmit` are asynchronous; `Stop` is synchronous to
permit a future durable handoff before exit. Currently all three return only an
incomplete-observation message. Post-effect scans remain advisory even after
implementation. Native policy/package loading, authenticated local transport,
exact content snapshots, and session state are still work in the
[architecture plan](../../docs/AgentArmor.md).
