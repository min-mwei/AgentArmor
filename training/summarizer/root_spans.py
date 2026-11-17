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

Version 2 (root-spans/v2, annotation(..., version=2) and pieces()) binds three things version 1
refuses, each by writing the text again and counting, never by looking for it; version 1 is
unchanged and stays the default, so earlier projections can be made again exactly:

    escapes   a string argument JSON writes with escapes (a quote, a backslash, a line break):
              value_spelled() records where each source character's spelling begins, so a span
              is the run of those spellings and reading it back as a JSON string gives exactly
              the source characters
    typed     an argument that is a number, true/false, null, a list or an object: placed whole,
              its text json.dumps of the value with the separators of the serialization; the
              author's text for it is its compact JSON, and only the whole value is a span
    patches   what a codex apply_patch adds to a file ("content") and the file it adds
              ("write"): the action as the summary writes it (patch_spelled()), each line of
              the file after a "+", so a span over several lines is several pieces (pieces())

Two arguments with the same value are two places: each is counted at its own key. expected()
gives the characters a field's interval becomes under the layout's recorded working directory
and home, and rendered_forms() the spellings a rendering may show them in: a regression of
the map compares a located place with them (forward, from the source), never the reverse.

Version 3 (root-spans/v3) changes only the place check: a string argument the action writes
as JSON and the session summary then quotes stands escaped twice ("quote(json)"); the check
admits that composition and, for every form, also undoes it (reads_back) and requires the
source characters back. Binding (pieces) is version 2's.

The command prints one annotation or one refusal for each line of SPANS.jsonl
({"question", "session", "event" (the step's id), "field", "start", "end"}); it replays the
root with the stand-in encoder, which is enough to find events: which text a packet then
shows, and where, is the map's to say, of the packet actually sent.
"""

import json
import sys
from typing import NamedTuple

EVENT = {"prompt": "request", "run": "proposed", "output": "result", "content": "proposed"}
ENTRY = {"prompt": "prompt", "run": "action", "output": "content", "content": "action"}
INPUT = "input."  # a field naming one argument of a structured call
VERSIONS = {1: "root-spans/v1", 2: "root-spans/v2", 3: "root-spans/v3"}
PATCH = (
    "apply_patch"  # the codex tool whose action is a patch (events.py writes it without its frame)
)


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


class Placed(NamedTuple):
    """One argument's place in a JSON object as written (value_spelled)."""

    text: str  # the whole object as JSON
    at: list  # [start, end): a string's body inside its quotes; any other value whole
    starts: (
        tuple  # a string: where each source character's spelling begins, then the end; else None
    )
    value: object


def json_spelling(value):
    """[(spelling, index)]: a string's JSON body as json.dumps writes it (non-ASCII kept), one
    item per source character. JSON escapes each character on its own, so the items joined are
    the body."""
    return [(json.dumps(char, ensure_ascii=False)[1:-1], index) for index, char in enumerate(value)]


def typed_source(value):
    """The author's text for an argument that is not a string: its compact JSON."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def value_spelled(mapping, key, sort_keys=False, separators=(",", ":")):
    """Placed for the argument key of mapping, counted while the object is written, or None.

    A string is placed character by character, escapes included (json_spelling); any other
    JSON value is placed whole. None when the key is absent or the object is not what JSON
    writes for it."""
    if not isinstance(mapping, dict) or key not in mapping:
        return None
    item, colon = separators
    text, found = "{", None
    for n, name in enumerate(sorted(mapping) if sort_keys else mapping):
        text += (item if n else "") + json.dumps(name, ensure_ascii=False) + colon
        value = mapping[name]
        body = json.dumps(value, ensure_ascii=False, sort_keys=sort_keys, separators=(item, colon))
        if name == key:
            if isinstance(value, str):
                starts, cursor = [], len(text) + 1
                for spelling, _ in json_spelling(value):
                    starts.append(cursor)
                    cursor += len(spelling)
                if cursor != len(text) + len(body) - 1:
                    return None
                found = ([len(text) + 1, cursor], (*starts, cursor))
            else:
                found = ([len(text), len(text) + len(body)], None)
        text += body
    text += "}"
    whole = json.dumps(mapping, ensure_ascii=False, sort_keys=sort_keys, separators=(item, colon))
    if found is None or text != whole:
        return None
    return Placed(text, found[0], found[1], mapping[key])


def spelled_interval(placed, start, end):
    """[start, end) of placed.text that spells [start, end) of the source value, or None.

    A string: from where character start's spelling begins to where character end - 1's
    ends; read back as a JSON string body it is exactly those characters. Any other value:
    only the whole of it, [0, len(typed_source(value)))."""
    if placed.starts is None:
        whole = (start, end) == (0, len(typed_source(placed.value)))
        return list(placed.at) if whole else None
    if type(start) is not int or type(end) is not int or not 0 <= start < end < len(placed.starts):
        return None
    low, high = placed.starts[start], placed.starts[end]
    if json.loads('"' + placed.text[low:high] + '"') != placed.value[start:end]:
        return None  # not reversible: never a place
    return [low, high]


def patch_spelled(path, content):
    """(text, [start, end) of the path, starts) for a codex apply_patch that adds one file, as
    the summary writes the action: "apply_patch *** Add File: PATH", then each line of the
    content after a "+", the lines joined by "\\n" (context_eval writes the patch, events.py
    leaves out its frame). starts holds, per content character, where it stands, or None for
    a line break the text does not keep as that very character."""
    text = PATCH + " *** Add File: "
    where = [len(text), len(text) + len(path)]
    text += path
    starts, broken = [], None
    for line in content.splitlines(keepends=True):
        body = line.splitlines()[0] if line.splitlines() else ""
        text += "\n"
        if broken is not None:
            starts[broken] = len(text) - 1
        text += "+"
        for char in body:
            starts.append(len(text))
            text += char
        ending = line[len(body) :]
        broken = len(starts) if ending == "\n" else None
        starts.extend([None] * len(ending))
    return text, where, starts


def runs(starts, start, end):
    """The pieces [[a, b], ...] of the text that hold source characters [start, end), each a
    run of characters that stand side by side; None when one of them is not in the text."""
    if not 0 <= start < end <= len(starts) or any(starts[i] is None for i in range(start, end)):
        return None
    out = [[starts[start], starts[start] + 1]]
    for i in range(start + 1, end):
        if starts[i] == out[-1][1]:
            out[-1][1] += 1
        else:
            out.append([starts[i], starts[i] + 1])
    return out


def expected(laid, session, step, field, start, end):
    """(the characters [start, end) of a root field become under the layout's recorded working
    directory and home, None) or (None, why). A non-string argument: its compact JSON."""
    from campaign_contract import HOME

    part = next((p for p in laid["parts"] if p["id"] == session), None)
    written = next((s for s in _steps(laid["campaign"], session) if s["id"] == step), None)
    if part is None or written is None:
        return None, "no_such_field"
    key = field[len(INPUT) :] if field.startswith(INPUT) else None
    source = (written.get("input") or {}).get(key) if key else written.get(field)
    if key and source is not None and not isinstance(source, str):
        text = typed_source(source)
        return (text, None) if (start, end) == (0, len(text)) else (None, "partial_typed_value")
    if not isinstance(source, str):
        return None, "no_such_field"
    found, why = filled(source, start, end, part["cwd"], HOME)
    if why:
        return None, why
    text, low, high = found
    return text[low:high], None


def _quote_spelled(text):
    from summarizer.sourcemap import spelled

    return "".join(piece for piece, _ in spelled(text, len(text) + 1))


def _json_spelled(text):
    return "".join(piece for piece, _ in json_spelling(text))


def rendered_forms(text, value=None, version=2):
    """The spellings a rendering may show text in: as it is, as text.quote() spells a session
    string (sourcemap.spelled), and as JSON spells a string value; for a non-string argument
    value, its JSON with either separator style. Version 3 adds the one composition a
    rendering writes: a string argument JSON-spelled inside the action's own text, and that
    text quoted again by the session summary ("quote(json)"), so a quotation mark or a
    backslash in the value stands escaped twice."""
    return set(_forms(text, value, version).values())


def _forms(text, value, version):
    forms = {"raw": text, "quote": _quote_spelled(text), "json": _json_spelled(text)}
    if value is not None and not isinstance(value, str):
        forms["typed"] = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    if version >= 3:
        forms["quote(json)"] = _quote_spelled(_json_spelled(text))
    return forms


def reads_back(shown, text, value=None, version=3):
    """The name of the declared rendering rule under which shown is text, checked BOTH ways: the
    rule's forward spelling of text equals shown, and undoing the rule on shown (one JSON string
    decoding per escaping layer) gives text back character for character (a typed value: its
    value). None when no rule does both. Version 3 and later."""
    if version < 3:
        raise ValueError("reads_back belongs to root-spans/v3")

    def undo(layers):
        out = shown
        for _ in range(layers):
            try:
                out = json.loads('"' + out + '"')
            except ValueError:
                return None
        return out

    for name, form in _forms(text, value, version).items():
        if form != shown:
            continue
        if name == "typed":
            try:
                back = json.loads(shown)
            except ValueError:
                back = None
            if back == value:
                return name
            continue
        layers = {"raw": 0, "quote": 1, "json": 1, "quote(json)": 2}[name]
        if undo(layers) == text:
            return name
    return None


def place_agrees(state, place, text, value=None, version=2):
    """True when the located place of a rendered state holds text in one of its rendered
    forms (the forward check of a map: from the source, never from the rendering). Version 3
    also requires the reverse check (reads_back) and admits the quote(json) composition."""
    low, high = place
    if not (0 <= low < high <= len(state)):
        return False
    if version >= 3:
        return reads_back(state[low:high], text, value, version) is not None
    return state[low:high] in rendered_forms(text, value)


def pieces(laid, summaries, session, step, field, start, end):
    """Version 2: ([annotation, ...], None) or (None, why). One annotation for each piece of
    the summary's entry that holds the span: one, except for a patch's content over several
    lines. Besides version 1's fields: "write" (the path a write adds), a string argument with
    escapes, a non-string argument whole, and a codex patch's content and path."""
    part = next((p for p in laid["parts"] if p["id"] == session), None)
    key = field[len(INPUT) :] if field.startswith(INPUT) else None
    if part is None or (field not in (*EVENT, "write") and not key):
        return None, "no_such_session" if part is None else "unsupported_field"
    written = next((s for s in _steps(laid["campaign"], session) if s["id"] == step), None)
    made = next((s for s in part["laid"]["case"]["steps"] if s["id"] == step), None)
    if not key and field not in ("content", "write"):
        note, why = annotation(laid, summaries, session, step, field, start, end)
        return (None, why) if note is None else ([note], None)
    source = (
        ((written or {}).get("input") or {}).get(key, None) if key else (written or {}).get(field)
    )
    if written is None or made is None or source is None:
        return None, "no_such_field"
    if key and not isinstance(source, str):
        if (start, end) != (0, len(typed_source(source))):
            return None, "partial_typed_value"
        if (made.get("input") or {}).get(key) != source:
            return None, "source_rewritten"
    else:
        if not isinstance(source, str):
            return None, "no_such_field"
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
            return None, "no_such_interval"
        from campaign_contract import HOME

        found, why = filled(source, start, end, part["cwd"], HOME)
        if why:
            return None, why
        text, start, end = found
        shown = (made.get("input") or {}).get(key) if key else made.get(field)
        if shown != text:
            return None, "source_rewritten"  # the layout wrote other characters than these
    seq = part["laid"]["where"].get(step, {}).get("proposed")
    summary = summaries.get(session)
    if seq is None or summary is None:
        return None, "not_replayed"
    entries = [e for e in summary["entries"] if e["seq"] == seq and e["type"] == "action"]
    if len(entries) != 1:
        return None, "source_rewritten"
    entry = entries[0]["text"]
    about = {"kind": "session", "session": summary["session"].get("session_id"), "seq": seq}
    about |= {"field": "text"}
    if key:
        placed, tool = value_spelled(made["input"], key), made.get("tool")
        if placed is None or not isinstance(tool, str):
            return None, "source_rewritten"
        call = f"{tool} {placed.text}"
        if entry != call and not entry.startswith(call + "\n"):
            return None, "source_rewritten"
        at = spelled_interval(placed, start, end)
        if at is None:
            return None, "partial_typed_value" if placed.starts is None else "source_rewritten"
        base = len(tool) + 1
        return [about | {"start": base + at[0], "end": base + at[1]}], None
    path, content = made.get("write"), made.get("content", "")
    if not isinstance(path, str) or not isinstance(content, str):
        return None, "source_rewritten"
    if entries[0].get("tool") == PATCH:
        text, where, starts = patch_spelled(path, content)
        if entry != text:
            return None, "source_rewritten"
        if field == "write":
            found = [[where[0] + start, where[0] + end]]
        else:
            found = runs(starts, start, end)
            if found is None:
                return None, "line_break_not_shown"
        return [about | {"start": a, "end": b} for a, b in found], None
    if field == "content":
        note, why = annotation(laid, summaries, session, step, field, start, end)
        return (None, why) if note is None else ([note], None)
    head = f"{entries[0].get('tool')} {path}: "
    if not entry.startswith(head):
        return None, "source_rewritten"
    low = len(entries[0].get("tool") or "") + 1
    return [about | {"start": low + start, "end": low + end}], None


def annotation(laid, summaries, session, step, field, start, end, version=1):
    """(the span as an annotation of the summary's event, None) or (None, why).

    laid is campaign_contract.layout() of the root and summaries what its replay gave.
    session and step are the root's own ids, field one of EVENT, and [start, end) code
    points of that field's text as the root holds it. version=2 reads it as pieces() does
    and refuses a span that is several pieces ("several_pieces")."""
    if version == 2:
        found, why = pieces(laid, summaries, session, step, field, start, end)
        if found is None:
            return None, why
        return (found[0], None) if len(found) == 1 else (None, "several_pieces")
    if version != 1:
        raise ValueError("unknown version")
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
