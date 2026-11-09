"""Lightweight hook compatibility CLI; no model or GPU runtime imported."""

import argparse
import json
import sys

from .wire import MAX_BYTES, read_json

REASON = "AgentArmor policy evaluation is not qualified; no clear verdict is available."
ADVISORY = "AgentArmor observation is incomplete; no protection claim is available."


def hook(event):
    if not isinstance(event, dict) or not isinstance(event.get("hook_event_name"), str):
        raise ValueError("invalid hook event")
    name = event["hook_event_name"]
    if name == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": name,
                "permissionDecision": "deny",
                "permissionDecisionReason": REASON,
            }
        }, 2
    if name == "PermissionRequest":
        return {
            "hookSpecificOutput": {
                "hookEventName": name,
                "decision": {"behavior": "deny", "message": REASON},
            }
        }, 2
    if name in ("PostToolUse", "UserPromptSubmit", "Stop"):
        return {"systemMessage": ADVISORY}, 0
    raise ValueError("unsupported hook event")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="report local CLI readiness; does not probe a server")
    hooks = commands.add_parser("hook", help="emit an unqualified denial or advisory")
    hooks.add_argument("--agent", choices=("codex_code", "claude_code"), required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result, code = (
                {
                    "runtime": "python",
                    "serving_backend": "vllm",
                    "model_loaded": False,
                    "policy_ready": False,
                    "diagnostic": True,
                },
                0,
            )
        else:
            result, code = hook(read_json(sys.stdin.buffer.read(MAX_BYTES + 1)))
        print(json.dumps(result, allow_nan=False), flush=True)
    except Exception:
        print("AgentArmor could not complete the check; this action is denied.", file=sys.stderr)
        code = 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
