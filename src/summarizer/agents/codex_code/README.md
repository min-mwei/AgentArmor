# Codex integration

[hooks.json.example](hooks.json.example) and [config.toml.example](config.toml.example) register
the summarizer's hook for every Codex event that carries session evidence. The hook only observes:
it appends the event to the session's log and returns. It prints nothing, never blocks or denies a
tool call, and leaves Codex's approval flow untouched.

1. Install the package in a trusted virtual environment and print the hook settings with the
   hook's absolute path filled in:

   ```sh
   .venv/bin/pip install -e '.[runtime]'
   .venv/bin/summarizer hooks-config --agent codex_code                  # hooks.json
   .venv/bin/summarizer hooks-config --agent codex_code --format toml    # config.toml tables
   ```

2. Save the JSON as `~/.codex/hooks.json`, or merge the TOML into `~/.codex/config.toml`. Install
   one form, not both, or every hook runs twice. Prefer the user-level location over a
   repository's `.codex/`.
3. Start the daemon with `.venv/bin/summarizer serve`, restart Codex, open `/hooks`, and review
   and trust the commands. Codex skips a hook until its exact definition is trusted.

Check the hook by hand; it should print nothing and exit 0:

```sh
printf '%s\n' '{"session_id":"check","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"pwd"}}' \
  | .venv/bin/summarizer-hook --agent codex_code; echo "exit $?"
.venv/bin/summarizer list
```

## What is observed

`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`,
`SubagentStart`, `SubagentStop`, `Stop`, `PostCompact` and `SessionEnd`, as described in the
[Codex hooks guide](https://learn.chatgpt.com/docs/hooks). Shell commands arrive as `Bash`, file
edits as `apply_patch`, and MCP tools under their own names.

Codex has no event for loading instruction files, so at `SessionStart` the hook reads the
`AGENTS.md` (or `AGENTS.override.md`) files Codex reads: the one in `~/.codex`, then one per
directory from the repository root down to the working directory. Other file names configured as
project docs are not found.

Not observed, because no hook delivers them: hosted tools such as web search, cloud sessions, MCP
tool descriptions, and Codex's messages between tool calls. `codex exec` reports its permission
mode as `bypassPermissions`; the summary records that as context.

Checked against Codex CLI 0.167.0.
