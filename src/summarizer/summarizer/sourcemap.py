"""Where each character of a rendered text came from. Standard library only.

A span of evidence is an interval of the exact text a model was sent. This module says what
that interval is: characters of one field of one event of one session, a rule, a host fact,
the proposed call, or words the renderer wrote itself. It answers both ways: resolve() from
an interval of the text to its sources, locate() from an interval of a source to where the
text shows it.

Nothing is found by searching. While a text is rendered a second time under tracing(),
every session string that text.quote() is told the origin of is replaced by a stand-in of
the same length that names it, so the renderer makes the same choices in the same room and
each occurrence of a repeated phrase keeps its own identity. The stand-ins are then
exchanged for the literals. The result must equal the first rendering character for
character, or there is no map. Inside a literal the map is made by spelling the source out
again, one character at a time, the way quote() does (visible characters, the cut, JSON),
and comparing: a literal that cannot be reproduced is unmapped, never estimated.

A map holds offsets and identities, no text: not the state's, not a source's. Offsets are
half-open intervals of Unicode code points. Source offsets count the field as the summary
holds it, which is after redaction: an interval over a redaction marker is an interval of
the marker, and what the marker replaced is not here to be reconstructed.

What a map cannot say it says: "unmapped" is session text whose origin was not given (a
quote made without one, or a literal that did not reproduce), and resolve() returns it as
that. "synthesized" is the renderer's own writing: titles, counts, marks, quotation marks,
the cut sign, and short plain words (a tool's name, a status) that the renderer prints
without quotes. The two quotation marks around a mapped string carry role "quote" and the
cut sign role "cut": they are the serializer's delimiters, and a field's value ends where
its closing mark begins (boundaries()).

A map is provenance and nothing more. That an interval is the words of a tool result says
who wrote the words (its trust is on every resolved piece), never that what they claim is
true, authorized, or happened.
"""

import bisect
import contextlib
import hashlib
import json
import re

from .facts import visible
from .text import TRACE

OPEN, PAD, CLOSE = "\ue000", "\ue001", "\ue002"  # a stand-in: OPEN, its number, PADs, CLOSE
_FIRST, _BASE = 0xE100, 0x1000  # digits of the number: U+E100 to U+F0FF
_PRIVATE = re.compile("[\ue000-\uf8ff]")
_STAND_IN = re.compile(f"{OPEN}([\ue100-\uf0ff]+){PAD}*{CLOSE}")
_LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"')
SESSION, UNMAPPED, SYNTHESIZED = "session", "unmapped", "synthesized"
SCHEMA = "agentarmor-source-map/v1"


class Trace:
    """The quotes of one rendering, and the stand-ins that mark where each one went."""

    def __init__(self):
        self.quotes, self.broken, self.session, self.own = [], False, None, {}

    def entering(self, summary):
        """The summary whose entries the next quotes are of (text._lines)."""
        self.session = summary["session"].get("session_id")
        self.own = {id(entry): entry for entry in summary["entries"]}

    def _origin(self, source, of):
        """Where a quoted string came from, or None when what was said of it does not hold."""
        if of is None or self.session is None:
            return None
        entry, field, index, start = (*of, None, None)[:4]
        held = entry.get(field) if self.own.get(id(entry)) is entry else None
        if index is not None:
            held = held[index] if isinstance(held, list) and index < len(held) else None
        if not isinstance(held, str) or held[start or 0 :] != source:
            return None
        origin = {"session": self.session, "seq": entry["seq"], "type": entry["type"]}
        origin |= {"field": field if index is None else f"{field}[{index}]"}
        origin |= {"trust": entry.get("trust"), "start": start or 0}
        ref = entry.get("ref")  # a piece of content: where in its event's field it sits
        if field == "text" and isinstance(ref, dict):
            origin["ref"] = {key: ref.get(key) for key in ("field", "start", "end")}
        return origin

    def stand_in(self, literal, source, limit, of):
        """What text.quote() returns while tracing: as long as the literal, naming it."""
        if _PRIVATE.search(literal):  # session text may hold the very characters used here
            self.broken = True
            return literal
        number, tag = len(self.quotes), ""
        while True:
            tag = chr(_FIRST + number % _BASE) + tag
            number //= _BASE
            if not number:
                break
        if len(literal) < len(tag) + 2:
            return literal  # too short to carry a number: left as it is, unmapped
        self.quotes.append((literal, source, limit, self._origin(source, of)))
        return OPEN + tag + PAD * (len(literal) - len(tag) - 2) + CLOSE

    def restore(self, traced):
        """(the text with its literals back, [(start, quote)]) or None for a broken trace."""
        if self.broken:
            return None
        placed = []

        def literal(found):
            number = 0
            for digit in found.group(1):
                number = number * _BASE + ord(digit) - _FIRST
            if number >= len(self.quotes) or len(self.quotes[number][0]) != len(found.group()):
                self.broken = True
                return found.group()
            placed.append((found.start(), self.quotes[number]))
            return self.quotes[number][0]

        text = _STAND_IN.sub(literal, traced)
        return None if self.broken or _PRIVATE.search(text) else (text, placed)


@contextlib.contextmanager
def tracing():
    """Render inside this to learn where every quote went: yields the Trace."""
    trace = Trace()
    token = TRACE.set(trace)
    try:
        yield trace
    finally:
        TRACE.reset(token)


def spelled(source, limit):
    """[(characters, index in the source or None)]: a quote's body as text.quote() writes it.

    One item for each character that survives being made visible and cut, with its JSON
    spelling; None is the cut sign, which is no character of the source.
    """
    shown = [(char, index) for index, one in enumerate(source) for char in visible(one)]
    cut = len(shown) > limit
    kept = shown[: limit - 1] if cut else shown
    return [(json.dumps(c, ensure_ascii=False)[1:-1], i) for c, i in kept] + [("…", None)] * cut


def _literal_segments(at, quote):
    """The segments of one placed literal, or one unmapped segment."""
    literal, source, limit, origin = quote
    pieces = spelled(source, limit)
    whole = [{"at": [at, at + len(literal)], "kind": UNMAPPED}]
    if origin is None or '"' + "".join(text for text, _ in pieces) + '"' != literal:
        return whole
    base = origin["start"]
    about = {key: value for key, value in origin.items() if key != "start"} | {"kind": SESSION}
    out, cursor = [{"at": [at, at + 1], "kind": SYNTHESIZED, "role": "quote"}], at + 1
    for text, index in pieces:
        end = cursor + len(text)
        last = out[-1]
        if index is None:
            out.append({"at": [cursor, end], "kind": SYNTHESIZED, "role": "cut"})
        elif last["kind"] == SESSION and last["source"][1] - 1 == base + index:
            last["at"][1], last["exact"] = end, False  # more of the same source character
        elif (
            last["kind"] == SESSION
            and last["exact"]
            and len(text) == 1
            and last["source"][1] == base + index
        ):
            last["at"][1], last["source"][1] = end, base + index + 1
        else:
            exact = len(text) == 1
            out.append(about | {"at": [cursor, end], "source": [base + index, base + index + 1]})
            out[-1]["exact"] = exact
        cursor = end
    return [*out, {"at": [cursor, cursor + 1], "kind": SYNTHESIZED, "role": "quote"}]


def of_rendering(text, placed):
    """Segments covering a rendered text: its placed literals, the literals nobody gave an
    origin for (unmapped), and the renderer's own words between them (synthesized)."""
    out, cursor = [], 0

    def between(end):
        for found in _LITERAL.finditer(text, cursor, end):
            out.append({"at": [found.start(), found.end()], "kind": UNMAPPED})

    for at, quote in sorted(placed, key=lambda item: item[0]):
        between(at)
        out.extend(_literal_segments(at, quote))
        cursor = at + len(quote[0])
    between(len(text))
    return out


class SourceMap:
    """The map of one exact text. Made by map_of(); a new text is a new map."""

    def __init__(self, text, segments, cutoff=None):
        self.sha256 = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        self.chars, self.cutoff = len(text), cutoff
        filled, cursor = [], 0
        for segment in sorted(segments, key=lambda item: item["at"][0]):
            start, end = segment["at"]
            if start < cursor or end > len(text) or start >= end:
                raise ValueError("segments overlap or leave the text")
            if start > cursor:
                filled.append({"at": [cursor, start], "kind": SYNTHESIZED})
            filled.append(segment)
            cursor = end
        if cursor < len(text):
            filled.append({"at": [cursor, len(text)], "kind": SYNTHESIZED})
        self.segments, self._starts = filled, [item["at"][0] for item in filled]

    def resolve(self, start, end):
        """What the interval [start, end) of the text is, piece by piece, in order.

        Each piece is a segment's identity with "at" (the part of the interval it covers)
        and, for session text and the call, "source": the interval of the source field that
        part shows. Inside an escape or a spelled-out character the source is the whole
        character. Raises ValueError for an interval that is not in the text.
        """
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= self.chars:
            raise ValueError("no such interval of this text")
        out = []
        for segment in self.segments[bisect.bisect_right(self._starts, start) - 1 :]:
            low, high = segment["at"]
            if low >= end:
                break
            piece = dict(segment) | {"at": [max(low, start), min(high, end)]}
            if "source" in segment and segment.get("exact"):
                first = segment["source"][0]
                piece["source"] = [first + piece["at"][0] - low, first + piece["at"][1] - low]
            elif "source" in segment:
                piece["source"] = list(segment["source"])
            piece.pop("exact", None)
            out.append(piece)
        return out

    def boundaries(self):
        """Offsets where one source ends and another thing begins: both ends of every mapped
        quotation and of every framed piece (a rule, the host facts, the call). An interval
        that crosses one joins two sources or a source and the renderer's own words."""
        edges = set()
        for before, after in zip(self.segments, self.segments[1:], strict=False):
            mine = [
                {k: v for k, v in s.items() if k not in ("at", "source", "exact")}
                for s in (before, after)
            ]
            if mine[0] != mine[1]:
                edges.add(after["at"][0])
        return sorted(edges)

    def told(self):
        """The map as JSON data: identities and offsets, no text."""
        return {
            "schema": SCHEMA,
            "sha256": self.sha256,
            "chars": self.chars,
            "cutoff": self.cutoff,
            "segments": self.segments,
        }

    @classmethod
    def read(cls, told):
        """A map from told(); it is of the text told() was of, which is not here to check."""
        if not isinstance(told, dict) or told.get("schema") != SCHEMA:
            raise ValueError("not a source map")
        found = cls.__new__(cls)
        found.sha256, found.chars, found.cutoff = told["sha256"], told["chars"], told["cutoff"]
        found.segments = told["segments"]
        found._starts = [item["at"][0] for item in found.segments]
        return found

    def missing(self, session, seq, field, start, end, kind=SESSION, ref=None):
        """Why locate() finds nothing: "not_shown" (no quotation of that field is mapped
        here) or "not_whole" (one is, and it does not show all of the interval). None when
        locate() finds a place."""
        if self.locate(session, seq, field, start, end, kind, ref):
            return None
        shown = any(
            (s["kind"], s.get("session"), s.get("seq"), s.get("field"), s.get("ref"))
            == (kind, session, seq, field, ref)
            for s in self.segments
        )
        return "not_whole" if shown else "not_shown"

    def locate(self, session, seq, field, start, end, kind=SESSION, ref=None):
        """Every interval of the text that shows [start, end) of one source field whole, in
        text order: one for each place the field is quoted. Empty when no place shows all of
        it (cut, left out, or unmapped): then the target is unavailable, not somewhere near.
        ref is a piece of content's own ({"field", "start", "end"}): an event's result can
        arrive as several pieces, and each is an entry with its own text."""
        found, run = [], []
        for segment in [*self.segments, {"kind": None}]:
            same = (
                segment["kind"] == kind
                and (segment.get("session"), segment.get("seq")) == (session, seq)
                and segment.get("field") == field
                and segment.get("ref") == ref
                and "source" in segment
            )
            if same and (not run or run[-1]["source"][1] == segment["source"][0]):
                run.append(segment)
                continue
            first = _edge(run, start, 0)
            last = _edge(run, end - 1, 1)
            if start < end and first is not None and last is not None:
                found.append([first, last])
            run = [segment] if same else []
        return found


def _edge(run, offset, side):
    """Where in the text the source character at offset begins (side 0) or ends (side 1),
    within one unbroken quotation of a field; None when the quotation does not hold it."""
    for segment in run:
        low, high = segment["source"]
        if low <= offset < high:
            if segment["exact"]:
                return segment["at"][0] + offset - low + side
            return segment["at"][side]
    return None


def map_of(render, plain=None, cutoff=None, frame=None):
    """The SourceMap of a rendering, or None when it cannot be made exactly.

    render() returns the rendered text; plain is that text when the caller already has it,
    and render() is then called once, traced. frame(text) says what the rendering sits in:
    [(kind, about, text)] in order, the rendering itself as (None, None, text) and every
    other piece with what it is. The pieces joined are the mapped text, which a caller
    compares with what it sent. Without a frame the rendering is the whole text.
    """
    plain = render() if plain is None else plain
    with tracing() as trace:
        try:
            traced = render()
        except Exception:  # noqa: BLE001 - a rendering that trips on a stand-in has no map
            return None
    restored = trace.restore(traced)
    if restored is None or restored[0] != plain:
        return None
    segments, cursor, whole = [], 0, []
    for kind, about, text in frame(plain) if frame else [(None, None, plain)]:
        if kind is None:
            for segment in of_rendering(plain, restored[1]):
                at = [segment["at"][0] + cursor, segment["at"][1] + cursor]
                segments.append(segment | {"at": at})
        elif text:
            about = dict(about or {})
            if about.pop("exact", False):  # the piece is its source, character for character
                about |= {"source": [0, len(text)], "exact": True}
            segments.append(about | {"at": [cursor, cursor + len(text)], "kind": kind})
        cursor += len(text)
        whole.append(text)
    return SourceMap("".join(whole), segments, cutoff)
