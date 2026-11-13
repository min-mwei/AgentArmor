# Claude Code integration

[settings.json.example](settings.json.example) registers the summarizer's hook for every event
that carries session evidence. The hook only observes: it appends the event to the session's log
and returns. It prints nothing, never blocks or denies a tool call, and leaves Claude Code's
permission flow untouched.

1. Install the package in a trusted virtual environment and print the settings with the hook's
   absolute path filled in:

   ```sh
   .venv/bin/pip install -e '.[runtime]'
   .venv/bin/summarizer hooks-config --agent claude_code
   ```

2. Merge the printed `hooks` entries into `~/.claude/settings.json`, keeping what is already
   there. Prefer user-level or managed settings over a repository's `.claude/settings.json`: a
   repository you do not control should not decide what runs on your machine.
3. Start the daemon with `.venv/bin/summarizer serve`, restart Claude Code, and check the
   registered commands with `/hooks`.

The handlers use exec form (`command` plus `args`), so no shell sits between Claude Code and the
hook. They run synchronously: each event is on disk before the session moves on, which costs about
25 ms. `SessionEnd` carries no `timeout` so that it keeps Claude Code's short default.

Check the hook by hand; it should print nothing and exit 0:

```sh
printf '%s\n' '{"session_id":"check","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"pwd"}}' \
  | .venv/bin/summarizer-hook --agent claude_code; echo "exit $?"
.venv/bin/summarizer list
```

## What is observed

`SessionStart`, `InstructionsLoaded`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`,
`PermissionDenied`, `PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop`, `Stop`,
`PostCompact`, `ConfigChange` and `SessionEnd`, as described in the
[hooks reference](https://code.claude.com/docs/en/hooks). For `InstructionsLoaded` the hook also
reads the instruction file, since Claude Code takes it as authority without a tool call.

Not observed, because no hook delivers them: files attached with `@` (set
`CLAUDE_CODE_DISABLE_FILE_MENTIONS=1` to make Claude read them with its file tools instead), MCP
tool descriptions, and Claude's messages between tool calls. Cloud sessions do not read your local
settings.

Checked against Claude Code 2.1.321.
