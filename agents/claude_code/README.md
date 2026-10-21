# Claude Code integration

[settings.json.example](settings.json.example) configures synchronous
`PreToolUse` authorization, asynchronous `PostToolUse`/`UserPromptSubmit` scans,
and end-of-turn `Stop` submission. The runner reads hook JSON from stdin and uses
the shared standard-library client. See the [Claude Code hook reference](https://code.claude.com/docs/en/hooks).

1. Start the [Armor service](../../armor/README.md). Export `AGENTARMOR_URL`,
   `AGENTARMOR_POLICY_FILE`, and `AGENTARMOR_TASK` in the shell that launches
   Claude Code. Set `AGENTARMOR_API_KEY` when the server requires a bearer token.
   Customize [the JSON policy template](../policy.example.json), including its rule tiers.
2. Replace `/ABSOLUTE/PATH/AgentArmor` with this checkout's absolute path.
3. Merge the template's `hooks` entries into the target workspace's
   `.claude/settings.json` or your user settings at `~/.claude/settings.json`.
   Preserve existing settings and avoid installing duplicate hook entries.
4. Start/restart Claude Code and inspect the hooks with `/hooks`.

A denied check emits `hookSpecificOutput.permissionDecision: deny`; an allowed
check emits nothing, preserving Claude Code's permission flow. Missing operator
context, malformed replies, and service errors cause an explicit denial.

```sh
printf '%s\n' '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"pwd"}}' \
  | python3 /absolute/path/to/AgentArmor/agents/claude_code/hook.py
```

## Asynchronous content scanning

After native `Read` calls, the background adapter reads the indicated UTF-8 file
and submits its text to `/v1/scans`. This checks the whole current file, including
when the tool read only a range. File changes between the read and the hook can
affect the scanned version. Other tools are scanned from `tool_response`, which
covers shell and MCP file reads when their results contain the text.

Chat hooks scan the raw `transcript_path` log at prompt submission and turn end.
These reuse v0's indirect-document detector experimentally; the checkpoint has
no evidence head or validated multi-turn/direct-chat detector.
The pending prompt is appended to the submitted log on `UserPromptSubmit`.
`Stop` synchronously submits the final log before CLI exit and returns a scan ID
without waiting for inference. The server continues the scan asynchronously;
poll the ID through the API to read its result.
Completed suspicious scans return advisory context; they cannot prevent the
completed read or subsequent actions taken before a warning arrives. Async hook
failures report an incomplete scan and do not emit a synchronous denial.

The hook client refuses files/logs over 1 MB or invalid UTF-8. Use
`agentarmor scan PATH --source chat_log` for explicit asynchronous log submission.
The task environment is static for the session; update it when authorization
changes. These templates do not install a filesystem monitor or reconstruct
the complete session permission history.
