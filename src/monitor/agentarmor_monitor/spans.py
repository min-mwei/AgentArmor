"""What a decoded evidence interval is once the renderer's own marks are known. Standard library.

An evidence head scores token pairs, and its decoder returns the characters a pair of tokens
covers (the hull). A tokenizer often joins a value to the quotation mark beside it, so the hull
of a value is then the value and that mark. Kai's training reads keep two measures apart for
that reason, and so does a record here:

    strict      the interval as decoded: token-aligned, nothing added, nothing removed. It is
                what the service returned and what a training read's decode returns for the
                same scores. It needs no layout and is always kept.
    projected   the same interval read under the rule the head's targets were projected by
                (value-wrapper-quote-v1): as a value and that value's own wrapper mark(s).
                The value is the interval less a mark at either end, and a mark is taken off
                only when the renderer wrote it around that very value.

This is the inverse of the training projection (Kai's kai.spans.targets), which widens an
annotated interval to its token hull and allows only that same mark: an interval v becomes the
hull h there exactly when value(h) is v here. Nothing is found by the look of the text. The
marks are the state's source map's: the two quotation marks text.quote() wrote around a mapped
session string ("quote"), and the two this server's JSON wrote around a string value of the
proposed call's arguments ("call-argument-value"). A quotation mark inside session text, a
rule, an unmapped literal or a value (escaped) is content and is never a mark. A mark of
another value is never taken: an interval that begins or ends with one, or holds its value's
mark and runs out of that value, has no projected reading ("mark_of_another_value") and
stays strict. A mark is taken off only if one token holds it and the value's character
beside it, which is the only way a training target came to include it; the service says
where those tokens stand ("edges"). Without a map there is no layout and no projected
reading at all.

A projected reading says how an interval lies against the renderer's marks. It does not say
the interval is right, that it is all the evidence there is, or that the packet it is of was
complete.
"""

import hashlib
import json

EXACT, WRAPPER = "exact", "value-wrapper-quote-v1"
ESCAPE = "serializer-escape-remainder-v1"
QUOTE, CALL_VALUE = "quote", "call-argument-value"
SOURCES = (QUOTE, CALL_VALUE)
LAYOUT_SCHEMA = "kai-span-layout/v1"


def wrappers(found, state, sources=(QUOTE,)):
    """{position of a mark: (start, end) of the value it encloses}, for the marks the renderer
    wrote in this state, from its map (summarizer.sourcemap.SourceMap).

    The same marks, from the same segments, as the layout the head's targets were projected
    with (training/summarizer/span_targets.layout; with CALL_VALUE in sources, its
    call_values). A quotation that was cut has no closing mark: the cut sign stands before
    it. A state the map is not of, or a source this code does not know, raises.
    """
    digest = hashlib.sha256(state.encode("utf-8", "surrogatepass")).hexdigest()
    if digest != found.sha256 or len(state) != found.chars:
        raise ValueError("this map is of another state")
    if not sources or set(sources) - set(SOURCES) or QUOTE not in sources:
        raise ValueError("unknown wrapper sources")
    marks, segments = {}, found.segments

    def mark(at, start, end):
        if state[at] != '"' or at in marks or at not in (start - 1, end):
            raise ValueError("a wrapper is the quotation mark immediately around its value")
        marks[at] = (start, end)

    for index, segment in enumerate(segments):
        low, high = segment["at"]
        call = (segment["kind"], segment.get("field")) == ("call", "arguments")
        if call and segment.get("exact") and CALL_VALUE in sources:
            quoted = escaped = False
            opened = None
            for at in range(low, high):
                char = state[at]
                if quoted:
                    closing = char == '"' and not escaped
                    escaped = char == "\\" and not escaped
                    if closing:
                        quoted = False
                        rest = state[at + 1 : high].lstrip(" ")
                        if not rest.startswith(":") and at > opened + 1:  # a value, not empty
                            mark(opened, opened + 1, at)
                            mark(at, opened + 1, at)
                elif char == '"':
                    quoted, opened = True, at
        if segment.get("role") != "quote" or state[low:high] != '"':
            continue
        after = segments[index + 1 :]
        if after and after[0]["kind"] == "session":  # an opening mark: its value runs on
            end = low + 1
            for piece in after:
                if piece["kind"] != "session":
                    break
                end = piece["at"][1]
            closing = next((p for p in after if p["kind"] != "session"), None)
            mark(low, low + 1, end)
            if closing and closing.get("role") == "quote" and closing["at"][0] == end:
                mark(end, low + 1, end)
    return marks


def map_sha256(found):
    """A source map by hash: of what it tells (told()), which holds offsets and no text."""
    told = json.dumps(found.told(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(told.encode("utf-8")).hexdigest()


def map_identity(found):
    """What a question's intervals were resolved through: the map by hash, the state it is of,
    and what it places events against (the call under decision, and the time each other
    session was cut at). With it a reader can check again, from the record alone, that every
    source kept beside an interval is from before the call (sourcemap.order). No text."""
    return {
        "map_sha256": map_sha256(found),
        "state_sha256": found.sha256,
        "state_chars": found.chars,
        "cutoff": found.cutoff,
        "sessions": found.sessions,
    }


def identity(found, sources):
    """What a layout was taken from: the state and the map, by hash, and whose marks."""
    return {
        "schema": LAYOUT_SCHEMA,
        "state_sha256": found.sha256,
        "map_sha256": map_sha256(found),
        "wrapper_sources": list(sources),
    }


def value(span, state, marks, edges=None):
    """One decoded interval read under WRAPPER: {"rule", "value", "marks"}, or {"rule": None,
    "why"} when it has no such reading.

    rule is EXACT when neither end of the interval is a mark (the value is the interval) and
    WRAPPER when a mark was taken off: the opening mark of the value that begins right after
    it, the closing mark of the value that ends right before it, and only if the rest of the
    interval lies inside that value and one token holds the mark and the character of the
    value beside it. That last condition is the training projection's own: a value is widened
    to a mark there only because its token reaches the mark, so an interval with a mark that
    is a token of its own is no value's projection ("mark_not_joined"). edges are the places
    of the tokens holding the interval's first and last character, from the service; without
    them an interval with a mark at an end has no reading ("no_token_edges").
    """
    start, end = span
    first, last = marks.get(start), marks.get(end - 1)
    low = start + (first is not None)
    high = end - (last is not None)
    if low >= high:
        return {"rule": None, "why": "only_marks"}
    if edges is None and (first is not None or last is not None):
        return {"rule": None, "why": "no_token_edges"}
    if first is not None and not edges[0][0] <= start < low < edges[0][1]:
        return {"rule": None, "why": "mark_not_joined"}
    if last is not None and not edges[1][0] < high < end <= edges[1][1]:
        return {"rule": None, "why": "mark_not_joined"}
    if first is not None and (first[0] != low or first[1] < high):
        return {"rule": None, "why": "mark_of_another_value"}
    if last is not None and (last[1] != high or last[0] > low):
        return {"rule": None, "why": "mark_of_another_value"}
    if state[low].isspace() or state[high - 1].isspace():
        return {"rule": None, "why": "space_inside_the_mark"}
    taken = [at for at, held in ((start, first), (end - 1, last)) if held is not None]
    return {"rule": WRAPPER if taken else EXACT, "value": [low, high], "marks": taken}


def _control_escape(text):
    """Whether text is the JSON escape a quotation writes for one control character
    (\\n, \\t, \\u0001)."""
    if not text.startswith("\\") or len(text) < 2:
        return False
    try:
        char = json.loads('"' + text + '"')
    except ValueError:
        return False
    return len(char) == 1 and ord(char) < 0x20 and json.dumps(char)[1:-1] == text


def escapes(found, state):
    """[start, end, value_start, value_end] for every serializer escape in this state's mapped
    session values: a piece the map says spells ONE source character of a session field in more
    than one character of the state (not exact), when those characters are the canonical JSON
    escape of a control character, with the interval of the unbroken run of that field's mapped
    characters it stands in. Taken from the map, never from the look of the text: a content
    backslash or quotation mark (spelled \\\\, \\"), a spelled-out invisible character, the call's
    own JSON and any unmapped or synthesized text are never escapes. The same table the training
    projection used (AgentArmor training/summarizer/span_targets.escapes; Kai kai.spans.escapes)."""
    digest = hashlib.sha256(state.encode("utf-8", "surrogatepass")).hexdigest()
    if digest != found.sha256 or len(state) != found.chars:
        raise ValueError("this map is of another state")
    out, run = [], []

    def close():
        if run:
            low, high = run[0]["at"][0], run[-1]["at"][1]
            for piece in run:
                start, end = piece["at"]
                if not piece.get("exact") and _control_escape(state[start:end]):
                    out.append([start, end, low, high])

    for segment in [*found.segments, {"kind": None}]:
        mapped = segment["kind"] == "session" and "source" in segment
        same = (
            mapped
            and run
            and all(
                segment.get(key) == run[-1].get(key) for key in ("session", "seq", "field", "ref")
            )
            and run[-1]["source"][1] == segment["source"][0]
            and run[-1]["at"][1] == segment["at"][0]
        )
        if same:
            run.append(segment)
            continue
        close()
        run = [segment] if mapped else []
    return out


def escaped(span, state, table):
    """One decoded interval read under ESCAPE: the interval with the remainder of a serializer
    escape stripped at an edge that lies strictly inside one (its start inside an escape that
    ends where the value begins, its end inside an escape that begins where the value ends, the
    value within the escape's own quotation), then trimmed of white space as the decoder trims.
    The inverse of Kai's kai.spans.escapes.unproject. None when no edge lies inside an escape or
    nothing is left; a reading otherwise."""
    start, end = span
    taken = []
    for s, e, low, high in table:
        if s < start < e and low <= s and e < high:
            start = e
            taken.append([s, e])
        if s < end < e and low < s and e <= high:
            end = s
            taken.append([s, e])
    if not taken:
        return None
    while start < end and state[start].isspace():
        start += 1
    while end > start and state[end - 1].isspace():
        end -= 1
    if start >= end:
        return {"rule": None, "why": "only_escape"}
    return {"rule": ESCAPE, "value": [start, end], "marks": [], "escapes": taken}


def projected(spans, state, found, targets, edges=None):
    """A question's strict intervals read under the rule its head's targets were projected by.

    targets is what the package said of the head ({"rule", "wrapper_sources"} and, when its
    targets could carry an escape's remainder, "escapes": ESCAPE), or None;
    edges the service's token places for each interval's two ends, or None.
    Returns None when nothing was said, or there is no map: strict only. A rule or a source
    this code does not have is named and not applied. Otherwise {"rule", "layout", "spans"},
    one reading for each interval in order, each with the interval it is a reading of.
    """
    if targets is None or found is None:
        return None
    rule, sources = targets.get("rule"), targets.get("wrapper_sources")
    known = isinstance(sources, list) and sources and not set(sources) - set(SOURCES)
    if rule != WRAPPER or not known or QUOTE not in sources:
        return {"rule": rule, "status": "unknown_rule"}
    marks = wrappers(found, state, sources)
    ends = edges if edges is not None else [None] * len(spans)
    told = targets.get("escapes")
    if told is not None and told != ESCAPE:
        return {"rule": rule, "status": "unknown_rule", "escapes": told}
    table = escapes(found, state) if told == ESCAPE else []
    read = []
    for span, pair in zip(spans, ends, strict=True):
        item = escaped(span, state, table) if table else None
        if (
            item is not None
            and item.get("value")
            and (item["value"][0] in marks or item["value"][1] - 1 in marks)
        ):
            item = {
                "rule": None,
                "why": "escape_and_mark",
            }  # both at once is no single rule's reading
        read.append(
            {"at": list(span)} | (item if item is not None else value(span, state, marks, pair))
        )
    return {"rule": WRAPPER, "layout": identity(found, sources), "spans": read} | (
        {"escapes": ESCAPE} if told else {}
    )
