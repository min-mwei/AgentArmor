"""Read supported native transcript messages without turning tool output into user authority."""

import json

from armor.scan_client import read_text_file


def transcript_history(path):
    turns = []
    pending_event = None
    for line in read_text_file(path).splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        message = entry.get("message", entry.get("payload", entry))
        if not isinstance(message, dict):
            continue
        kind = message.get("type")
        role = message.get("role")
        if kind in ("function_call_output", "custom_tool_call_output"):
            pending_event = None
            turns.append({"role": "tool", "content": json.dumps(message, ensure_ascii=False)})
            continue
        if kind in ("function_call", "custom_tool_call"):
            pending_event = None
            turns.append({"role": "assistant", "content": json.dumps(message, ensure_ascii=False)})
            continue
        if entry.get("type") == "event_msg" and kind in ("user_message", "agent_message"):
            text = message.get("message")
            if not isinstance(text, str):
                raise ValueError("Unsupported transcript message")
            role = "user" if kind == "user_message" else "assistant"
            turns.append({"role": role, "content": text})
            pending_event = len(turns) - 1
            continue
        if role not in ("user", "assistant", "tool", "system", "developer"):
            if kind == "message":
                raise ValueError("Unsupported transcript message role")
            continue
        content = message.get("content")
        canonical_text = content
        if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
            canonical_text = content[0].get("text")
        if pending_event is not None:
            if turns[pending_event] == {"role": role, "content": canonical_text}:
                turns.pop(pending_event)
            pending_event = None
        if isinstance(content, str):
            turns.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise ValueError("Transcript message has no supported content")
        for block in content:
            if not isinstance(block, dict):
                raise ValueError("Unsupported transcript content")
            kind = block.get("type")
            if kind in ("text", "input_text", "output_text") and isinstance(block.get("text"), str):
                turns.append({"role": role, "content": block["text"]})
            elif kind == "tool_result":
                turns.append({"role": "tool", "content": json.dumps(block, ensure_ascii=False)})
            elif kind == "tool_use" and role == "assistant":
                turns.append(
                    {"role": "assistant", "content": json.dumps(block, ensure_ascii=False)}
                )
            elif kind in ("thinking", "redacted_thinking", "reasoning") and role == "assistant":
                continue
            else:
                raise ValueError("Non-text transcript content cannot be silently dropped")
    return turns
