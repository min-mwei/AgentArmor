# Codex integration

The templates connect locally orchestrated Codex to the Armor service through
command hooks. `PreToolUse` checks authorization synchronously. Background
`PostToolUse` scans tool responses; `UserPromptSubmit` and `Stop` scan conversation
logs. Setup follows the [official Codex hook contract](https://learn.chatgpt.com/docs/hooks).

1. Start the [Armor service](../../armor/README.md) and export
   `AGENTARMOR_URL`, `AGENTARMOR_POLICY_FILE`, and `AGENTARMOR_TASK` in the shell
   that launches Codex. Export `AGENTARMOR_API_KEY` if required by the server.
   Use [the JSON policy template](../policy.example.json); v0 requires explicit rule tiers.
2. Replace `/ABSOLUTE/PATH/AgentArmor` in the templates with this checkout's
   absolute path. `hook.py` uses only the Python standard library.
3. Merge [config.toml.example](config.toml.example) into your trusted workspace's
   `.codex/config.toml`, or install [hooks.json.example](hooks.json.example) as
   `.codex/hooks.json`. User-level equivalents are under `~/.codex/`.
   Choose one representation so each hook runs once.
   Automation can also supply the same `[hooks]` configuration using `codex -c`.
   Our native-client harness uses this form without changing persistent user
   settings or project trust.
4. Start/restart Codex. Use `/hooks` to review and trust the exact configured
   hooks. Project-local hooks also need a trusted project configuration layer.

The denial hook returns `hookSpecificOutput.permissionDecision: deny`. An allow
returns no decision. Network failures, invalid input, or missing policy/task
produce an explicit denial. Keep the client timeout below the hook's 30 seconds.

For a direct smoke check, with the deny-only stub service running:

```sh
printf '%s\n' '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"pwd"}}' \
  | python3 /absolute/path/to/AgentArmor/agents/codex_code/hook.py
```

## Asynchronous content scanning

The supplied background hooks submit scan jobs and report suspicious results as
additional context. Tool-output scans cover file text returned by shell and MCP
read operations. Codex does not provide a universal file-read event, so this is
coverage of model-visible tool output, not an operating-system file monitor.

Chat hooks read `transcript_path` as UTF-8 text. They preserve the serialized log
rather than depending on its internal schema, and append the pending prompt on
`UserPromptSubmit`. `Stop` captures the log at the end of a turn and submits its
job synchronously before CLI exit; inference remains asynchronous on the server.
It returns the scan ID for API polling without waiting for the verdict. No files
are uploaded until the corresponding installed hook runs. Files/logs over 1 MB or
invalid UTF-8 are refused with an incomplete-scan warning.
Kai v0 supplies an indirect-document detector; scans of chat logs and arbitrary
tool outputs are experimental, and v0 has no evidence head or validated
multi-turn chat detector.

For an explicit file/log scan, use `agentarmor scan PATH --source text_file` or
`agentarmor scan PATH --source chat_log`. Both return queued job IDs immediately.

Command hooks require a local orchestration mode that supports them. Cloud
orchestration does not run these local hooks. Hook coverage and background output
delivery depend on the installed Codex version; verify with `/hooks` and a smoke
scan. The task environment is static for the session; update it when the authorized
task changes. This initial adapter does not reconstruct authorization history.
