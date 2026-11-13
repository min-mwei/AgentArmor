# Claude Code: the deny-all test fixture

> **Test only.** These templates call `agentarmor hook`, which denies every tool call and
> asks no model. They exist so that the hook entry point can be tested without a model.
> They are not an installation. To install AgentArmor for this agent, see the
> [guide one folder up](../README.md).

[settings.json.example](settings.json.example) invokes the installed Python CLI:

```sh
"/absolute/path/to/agentarmor" hook --agent claude_code
```

Install with `python3 -m pip install ./src/vllm` in a trusted virtual environment.
The entry point uses that environment’s Python interpreter and does not import
vLLM, Torch, or a GPU runtime. See the [root guide](../../../README.md).
See the [Claude Code hook contract](https://code.claude.com/docs/en/hooks).

**Current status:** hook policy evaluation is unqualified. Every
`PreToolUse` check returns an explicit denial and exit code 2. Observation hooks
report incomplete coverage; they do not scan files, tool output, or logs yet.
The CLI does not call the diagnostic model service.
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

Check the command directly before enabling the template:

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
implementation. Trusted policy/session assembly, authenticated transport, and exact content
snapshots remain prerequisites for connecting the hooks to model-backed enforcement.
See the [vLLM qualification requirements](../../../src/vllm/README.md#qualification).
