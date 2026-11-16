"""Evidence an author bound to a root, as annotations of the summary's events (span_targets.py).

    PYTHONPATH=src/summarizer:training/summarizer python training/summarizer/root_spans.py \
        ROOTS.jsonl ROOT_ID SPANS.jsonl

An author says where the evidence for a label is in the root it wrote: a session, one of its
steps, a field of that step and the characters [start, end) of that field's own text, as
code points. A source map (summarizer.sourcemap) knows a session's text by the summary's
event number instead. This is the step between: a root laid out for replay says which event
each step became (campaign_contract.layout, the "where" of every session), and a summary of
that replay holds the event's text. annotation() gives the span as span_targets.py reads it,
or says why it cannot:

    prompt   the user's request: the prompt entry of that event, character for character
    run      a shell call: the action entry, whose text is the tool's name and then the
             command; the span is counted from where the command begins
    output   a call's result: the piece of content that holds the span whole, with the
             piece's own place in the event (ref); a piece is kept without the blank
             space after it, and a span is never taken from that space

    input.K  a string argument K of a call the root wrote as a tool and an input: the action
             entry, whose text is the tool's name and then the input as compact JSON; the
             value's place is counted while that JSON is written again, and the entry must be
             exactly it, or it and then lines of its own in which the summary lists values
             that are not strings. A value JSON has to escape is not character for character
             its source ("source_rewritten")
    content  what a step the root wrote as a write put in the file: the action entry, whose
             text is the tool's name, the path, a colon and then the content without the blank
             space after it; a span is never taken from that space

Nothing is searched for. A field is taken only when the summary's entry holds its text as the
root wrote it (the whole prompt, the command as the tail of the action's text, a piece equal
to its part of the output): a field the hook redacted or the summary cut is
"source_rewritten", and the span is then unavailable, never moved to where similar words
stand. A step past the cutoff of the decision was never replayed ("not_replayed").

A root may write a session's working directory as {cwd} and the home folder as {home}; the
layout fills them in (campaign_contract._fill). Such a field is taken when filling the root's
text in the same way gives exactly the laid-out text, and the span's ends are moved by what
was filled in before them. A span that begins or ends inside a placeholder names no
characters of the filled text ("crosses_placeholder").

value_place() is the same counting for any JSON object this code can write again: where a
string value stands inside the serialization, for a caller that binds a copy by construction
(the proposed call's arguments).

The command prints one annotation or one refusal for each line of SPANS.jsonl
({"question", "session", "event" (the step's id), "field", "start", "end"}); it replays the
root with the stand-in encoder, which is enough to find events: which text a packet then
shows, and where, is the map's to say, of the packet actually sent.
"""

import json
import sys

EVENT = {"prompt": "request", "run": "proposed", "output": "result", "content": "proposed"}
ENTRY = {"prompt": "prompt", "run": "action", "output": "content", "content": "action"}
INPUT = "input."  # a field naming one argument of a structured call


def filled(text, start, end, cwd, home):
    """(the text as the layout fills it in, start, end there) or (None, why)."""
    marks, out, shift = {"{cwd}": cwd, "{home}": home}, [], 0
    place, low, high = 0, None, None
    while place <= len(text):
        mark = next((m for m in marks if text.startswith(m, place)), None)
        if place == start:
            low = place + shift
        if place == end:
            high = place + shift
        if place == len(text):
            break
        if mark is None:
            out.append(text[place])
            place += 1
            continue
        if place < start < place + len(mark) or place < end < place + len(mark):
            return None, "crosses_placeholder"
        out.append(marks[mark])
        shift += len(marks[mark]) - len(mark)
        place += len(mark)
    return ("".join(out), low, high), None


def value_place(mapping, key, sort_keys=False, separators=(",", ":")):
    """(the object as JSON text, [start, end) of the string value of key inside it), or None.

    Counted while the text is written, never looked for. None when the value is not a string
    or is one JSON writes with an escape: its characters are then not the source's."""
    if not isinstance(mapping, dict) or not isinstance(mapping.get(key), str):
        return None
    item, colon = separators
    text, at = "{", None
    for n, name in enumerate(sorted(mapping) if sort_keys else mapping):
        text += (item if n else "") + json.dumps(name, ensure_ascii=False) + colon
        body = json.dumps(
            mapping[name], ensure_ascii=False, sort_keys=sort_keys, separators=(item, colon)
        )
        if name == key:
            if body[1:-1] != mapping[name]:
                return None
            at = [len(text) + 1, len(text) + len(body) - 1]
        text += body
    text += "}"
    if text != json.dumps(
        mapping, ensure_ascii=False, sort_keys=sort_keys, separators=(item, colon)
    ):
        return None
    return text, at


def annotation(laid, summaries, session, step, field, start, end):
    """(the span as an annotation of the summary's event, None) or (None, why).

    laid is campaign_contract.layout() of the root and summaries what its replay gave.
    session and step are the root's own ids, field one of EVENT, and [start, end) code
    points of that field's text as the root holds it."""
    part = next((p for p in laid["parts"] if p["id"] == session), None)
    key = field[len(INPUT) :] if field.startswith(INPUT) else None
    if part is None or (field not in EVENT and not key):
        return None, "no_such_session" if part is None else "unsupported_field"
    written = next((s for s in _steps(laid["campaign"], session) if s["id"] == step), None)
    made = next((s for s in part["laid"]["case"]["steps"] if s["id"] == step), None)
    source = (written or {}).get("input", {}).get(key) if key else (written or {}).get(field)
    if written is None or not isinstance(source, str):
        return None, "no_such_field"
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
        return None, "no_such_interval"
    from campaign_contract import HOME

    found, why = filled(source, start, end, part["cwd"], HOME)
    if why:
        return None, why
    text, start, end = found
    shown = (made or {}).get("input", {}).get(key) if key else (made or {}).get(field)
    if shown != text:
        return None, "source_rewritten"  # the layout wrote other characters than these
    seq = part["laid"]["where"].get(step, {}).get("proposed" if key else EVENT[field])
    summary = summaries.get(session)
    if seq is None or summary is None:
        return None, "not_replayed"
    kind = "action" if key else ENTRY[field]
    entries = [e for e in summary["entries"] if e["seq"] == seq and e["type"] == kind]
    about = {"kind": "session", "session": summary["session"].get("session_id"), "seq": seq}
    about |= {"field": "text"}
    if key:
        placed = value_place(made["input"], key)
        tool = made.get("tool")
        if placed is None or not isinstance(tool, str) or len(entries) != 1:
            return None, "source_rewritten"
        text, call = entries[0]["text"], f"{tool} {placed[0]}"
        if text != call and not text.startswith(call + "\n"):
            return None, "source_rewritten"
        base = len(tool) + 1 + placed[1][0]
        return about | {"start": base + start, "end": base + end}, None
    if field == "prompt":
        if len(entries) != 1 or entries[0]["text"] != text:
            return None, "source_rewritten"
        return about | {"start": start, "end": end}, None
    if field == "content":
        path, kept = made.get("write"), text.rstrip()  # kept without the blank space after it
        if len(entries) != 1 or not isinstance(path, str) or end > len(kept):
            return None, "source_rewritten"
        if entries[0]["text"] != f"{entries[0].get('tool')} {path}: {kept}":
            return None, "source_rewritten"
        base = len(entries[0]["text"]) - len(kept)
        return about | {"start": base + start, "end": base + end}, None
    if field == "run":
        if len(entries) != 1 or not entries[0]["text"].endswith(text):
            return None, "source_rewritten"
        base = len(entries[0]["text"]) - len(text)  # after the tool's name
        return about | {"start": base + start, "end": base + end}, None
    held = []
    for entry in entries:  # the pieces of the result that are this output, in their places
        ref = entry.get("ref") or {}
        low, high = ref.get("start"), ref.get("end")
        if type(low) is not int or type(high) is not int:
            continue
        shown = low + len(entry["text"])  # a piece is kept without the blank space after it
        if text[low:shown] == entry["text"] and not text[shown:high].strip():
            held.append((shown, {key: ref.get(key) for key in ("field", "start", "end")}))
    if not held:
        return None, "source_rewritten"
    if len({ref["field"] for _, ref in held}) != 1:
        return None, "ambiguous_field"  # two fields of the event hold these very characters
    whole = [ref for shown, ref in held if ref["start"] <= start and end <= shown]
    if len(whole) != 1:
        return None, "crosses_pieces"
    low = whole[0]["start"]
    return about | {"start": start - low, "end": end - low, "ref": whole[0]}, None


def _steps(campaign, session):
    return next((s["steps"] for s in campaign["sessions"] if s["id"] == session), ())


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    import campaign_contract as cc
    from common import read_jsonl
    from summarizer.embedder import create
    from summarizer.summarize import Runtime

    root = next((r for r in read_jsonl(argv[0]) if r["id"] == argv[1]), None)
    if root is None:
        raise SystemExit("no such root")
    laid = cc.layout(root)
    summaries, _ = cc.replay(laid, Runtime(create("hash")), **cc.VARIANTS[0])
    for span in read_jsonl(argv[2]):
        where = (span["session"], span["event"], span["field"], span["start"], span["end"])
        note, why = annotation(laid, summaries, *where)
        told = {"question": span["question"]} | (note or {"unavailable": why})
        print(json.dumps(told, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
