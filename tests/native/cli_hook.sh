#!/bin/bash
set -eu
armor_binary=$1
for armor_agent in codex_code claude_code; do
  for armor_event in PreToolUse PermissionRequest; do
    set +e
    armor_output=$(printf '{"hook_event_name":"%s","tool_name":"Bash","tool_input":{}}' "$armor_event" |
      "$armor_binary" hook --agent "$armor_agent")
    armor_status=$?
    set -e
    test "$armor_status" -eq 2
    case "$armor_event:$armor_output" in
      PreToolUse:*'"permissionDecision":"deny"'*) ;;
      PermissionRequest:*'"behavior":"deny"'*) ;;
      *) exit 1 ;;
    esac
  done
done
set +e
armor_output=$(printf '{"hook_event_name":"PreToolUse","secret":"SYNTHETIC"' |
  "$armor_binary" hook --agent codex_code 2>&1)
armor_status=$?
set -e
test "$armor_status" -eq 2
case "$armor_output" in *SYNTHETIC*) exit 1 ;; esac
