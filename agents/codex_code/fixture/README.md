# Codex: the deny-all test fixture

> **Test only.** These templates call `agentarmor hook`, which denies every tool call and
> asks no model. They exist so that the hook entry point can be tested without a model.
> They are not an installation. To install AgentArmor for this agent, see the
> [guide one folder up](../README.md).

The templates invoke the installed Python CLI directly:

```sh
"/absolute/path/to/agentarmor" hook --agent codex_code
```

Install with `python3 -m pip install ./src/vllm` in a trusted virtual environment.
The entry point uses that environment’s Python interpreter and does not import
vLLM, Torch, or a GPU runtime. See the [root guide](../../../README.md).
Setup follows the [Codex hook contract](https://learn.chatgpt.com/docs/hooks).

**Current status:** hook policy evaluation is unqualified. Every
`PreToolUse` check returns an explicit denial and exit code 2. The observation
hooks report incomplete coverage; they do not scan content or queue work yet.
The CLI does not call the diagnostic model service.
These templates are for integration development and will block normal tool use.
`agentarmor doctor` reports `policy_ready: false`. They must not be presented as
a working policy-based allow/deny installation.

Do not merge these templates into the settings you work with: they would deny every
tool call. Use them only in a throwaway configuration made for an integration test, or
run the fixture's command directly, with the absolute path that `command -v agentarmor`
prints:

```sh
printf '%s\n' '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"pwd"}}' \
  | /absolute/path/to/agentarmor hook --agent codex_code
```

Expect `hookSpecificOutput.permissionDecision: deny` and exit code 2, because
model-backed hook enforcement is not qualified. A future qualified allow must emit no
permission decision and preserve Codex's existing permission flow.

`PostToolUse` and `UserPromptSubmit` are asynchronous; `Stop` is synchronous to
permit a future durable handoff before exit. Currently all three return only an
incomplete-observation message. Post-effect scans remain advisory even after
implementation. Local command hooks require supported local orchestration and
do not cover cloud or hosted tools where the hook is not invoked. See the
[vLLM qualification requirements](../../../src/vllm/README.md#qualification).
