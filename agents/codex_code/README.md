# Codex integration

The templates invoke the installed Python CLI directly:

```sh
"/absolute/path/to/agentarmor" hook --agent codex_code
```

Install with `python3 -m pip install ./src/vllm` in a trusted virtual environment.
The entry point uses that environment’s Python interpreter and does not import
vLLM, Torch, or a GPU runtime. See the [root guide](../../README.md).
Setup follows the [Codex hook contract](https://learn.chatgpt.com/docs/hooks).

**Current status:** hook policy evaluation is unqualified. Every
`PreToolUse` check returns an explicit denial and exit code 2. The observation
hooks report incomplete coverage; they do not scan content or queue work yet.
The CLI does not call the diagnostic model service.
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

Check the command directly before enabling the template:

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
[vLLM qualification requirements](../../src/vllm/README.md#qualification).
