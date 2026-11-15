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

Nothing is searched for. A field is taken only when the summary's entry holds its text as the
root wrote it (the whole prompt, the command as the tail of the action's text, a piece equal
to its part of the output): a field the layout filled in, the hook redacted or the summary
cut is "source_rewritten", and the span is then unavailable, never moved to where similar
words stand. A step past the cutoff of the decision was never replayed ("not_replayed").

The command prints one annotation or one refusal for each line of SPANS.jsonl
({"question", "session", "event" (the step's id), "field", "start", "end"}); it replays the
root with the stand-in encoder, which is enough to find events: which text a packet then
shows, and where, is the map's to say, of the packet actually sent.
"""

import json
import sys

EVENT = {"prompt": "request", "run": "proposed", "output": "result"}
ENTRY = {"prompt": "prompt", "run": "action", "output": "content"}


def annotation(laid, summaries, session, step, field, start, end):
    """(the span as an annotation of the summary's event, None) or (None, why).

    laid is campaign_contract.layout() of the root and summaries what its replay gave.
    session and step are the root's own ids, field one of EVENT, and [start, end) code
    points of that field's text as the root holds it."""
    part = next((p for p in laid["parts"] if p["id"] == session), None)
    if part is None or field not in EVENT:
        return None, "no_such_session" if part is None else "unsupported_field"
    written = next((s for s in _steps(laid["campaign"], session) if s["id"] == step), None)
    filled = next((s for s in part["laid"]["case"]["steps"] if s["id"] == step), None)
    if written is None or not isinstance(written.get(field), str):
        return None, "no_such_field"
    text = written[field]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        return None, "no_such_interval"
    if filled is None or filled.get(field) != text:
        return None, "source_rewritten"  # the layout filled something in: other characters
    seq = part["laid"]["where"].get(step, {}).get(EVENT[field])
    summary = summaries.get(session)
    if seq is None or summary is None:
        return None, "not_replayed"
    entries = [e for e in summary["entries"] if e["seq"] == seq and e["type"] == ENTRY[field]]
    about = {"kind": "session", "session": summary["session"].get("session_id"), "seq": seq}
    about |= {"field": "text"}
    if field == "prompt":
        if len(entries) != 1 or entries[0]["text"] != text:
            return None, "source_rewritten"
        return about | {"start": start, "end": end}, None
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
