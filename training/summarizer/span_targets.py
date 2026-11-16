"""Where annotated evidence stands in a rendered state: span targets by lookup, never by search.

    PYTHONPATH=src/summarizer python training/summarizer/span_targets.py MAP.json ANNOTATIONS.jsonl \
        [--policy select|all-canonical]

MAP.json is one state's source map as SourceMap.told() writes it (summarizer.sourcemap;
agentarmor_monitor.escalate.detect(maps=...) makes one for each request it builds). It holds
identities and offsets, no text. ANNOTATIONS.jsonl has one annotation to a line, each saying
what a question's answer rests on in terms of the source, not of the rendering:

    {"question": "decision", "kind": "session", "session": S, "seq": N, "field": "text",
     "start": 12, "end": 40}                      characters [start, end) of one event's field,
                                                   as code points of the field's own text
    ... and "ref": {"field", "start", "end"}       for a piece of content (its entry's ref)
    ... and "occurrence": 1                        which place, when the state quotes it twice
    {"question": "decision", "kind": "policy", "rule": "secrets"}        a rule, whole
    {"question": "decision", "kind": "host_fact"}                        the host facts, whole
    {"question": "decision", "kind": "call", "field": "arguments", "start": 2, "end": 9}
    {"question": "is_xpia_attempt", "empty": true}  a certified empty target for the question

    ... and "copies": [{"kind": "call", "field": "arguments", "start": 13, "end": 60}]
                                                   places of other pieces that hold these very
                                                   characters by construction (the caller's proof:
                                                   the proposed call's arguments quoting the
                                                   command of the action being decided)

Two policies say what is done when the state shows an annotated source more than once:

    select          one place: the annotation's "occurrence", or the only one; two places and
                    no choice make the question unavailable (the default)
    all-canonical   every genuine, complete copy is a target: each place the map shows that
                    source interval whole, and each of the annotation's "copies". A copy is
                    bound by construction to the same event and field. Equal words in another
                    event, another trust level or a quoted document are another source and are
                    never taken. A listed copy the state does not show whole makes the whole
                    question unavailable ("missing_copy"). Identical intervals count once.

Positive targets are original units only: what a session event said, a rule, the host facts,
the call. What the summarizer derived from them (observables, indicator bases, notes: the
map's "derived" pieces) and its own words ("synthesized") are never targets, under either
policy; an annotation of one is refused ("not_a_source_unit"). A question with more than
MAX_SPANS intervals is unavailable ("span_budget").

One line is printed for each question: its spans as half-open code-point intervals of the
state, sorted, or that it is unavailable and why. A question is complete or unavailable,
never partial: one annotation the state does not show whole, or shows twice without being
told which, makes the whole question unavailable, because a head trained on the rest would
be taught that the missing part is not evidence. Overlapping intervals are refused. A map
says whose words an interval is. It does not say the words are true.
"""

import hashlib
import json
import sys

from summarizer.sourcemap import SESSION, SourceMap

SELECT, ALL = "select", "all-canonical"
MAX_SPANS = 32  # what one question's readout can return (agentarmor_vllm.pairs)
UNITS = (SESSION, "policy", "host_fact", "call")


def places(found, note, policy=SELECT):
    """(intervals, None) for one annotation, or (None, why)."""
    kind = note.get("kind")
    if kind not in UNITS:
        derived = kind in ("derived", "synthesized", "unmapped")
        return None, "not_a_source_unit" if derived else "unknown_kind"
    if kind == SESSION:
        where = (note["session"], note["seq"], note["field"], note["start"], note["end"])
        at = found.locate(*where, ref=note.get("ref"))
        why = found.missing(*where, ref=note.get("ref"))
    elif kind in ("policy", "host_fact", "call"):
        same = [
            s
            for s in found.segments
            if s["kind"] == kind and all(s.get(key) == note.get(key) for key in ("rule", "field"))
        ]
        if "start" in note:  # part of a piece that is its source character for character
            at = [
                [s["at"][0] + note["start"], s["at"][0] + note["end"]]
                for s in same
                if s.get("exact") and 0 <= note["start"] < note["end"] <= s["source"][1]
            ]
        else:
            at = [list(s["at"]) for s in same]
        why = None if at else ("not_shown" if not same else "not_whole")
    if why:
        return None, why
    if policy == ALL:
        for copy in note.get("copies", ()):
            more, _ = places(found, {key: value for key, value in copy.items() if key != "copies"})
            if not more or copy.get("kind") not in UNITS:
                return None, "missing_copy"
            at = at + more
        return [list(a) for a in sorted({tuple(a) for a in at})], None
    if "occurrence" in note:
        chosen = note["occurrence"]
        return ([at[chosen]], None) if 0 <= chosen < len(at) else (None, "no_such_occurrence")
    return (at, None) if len(at) == 1 else (None, "ambiguous_occurrence")


def project(found, annotations, policy=SELECT):
    """{question: {"complete": True, "spans": [...]} or {"complete": False, "why": [...]}}."""
    if policy not in (SELECT, ALL):
        raise ValueError("unknown occurrence policy")
    out = {}
    for note in annotations:
        told = out.setdefault(note["question"], {"complete": True, "spans": [], "why": []})
        if note.get("empty"):
            told["empty"] = True
            continue
        at, why = places(found, note, policy)
        told["spans"] += at or []
        told["why"] += [why] if why else []
    for told in out.values():
        if policy == ALL:  # the same place reached from two annotations is one target
            told["spans"] = [list(a) for a in {tuple(a) for a in told["spans"]}]
        told["spans"].sort()
        mixed = told.pop("empty", False) and told["spans"]
        crossed = any(a[1] > b[0] for a, b in zip(told["spans"], told["spans"][1:], strict=False))
        told["why"] += ["empty_and_spans"] * bool(mixed) + ["overlapping_spans"] * crossed
        told["why"] += ["span_budget"] * (len(told["spans"]) > MAX_SPANS)
        if told["why"]:
            told |= {"complete": False, "spans": None}
        else:
            del told["why"]
    return out


def layout(found, state, call_values=False):
    """The renderer's own marks in this state, for a tokenizer that joins a value to them:

        structural   positions of the { } [ ] , the proposed call's arguments are written with,
                     outside its strings (the arguments are JSON this server serialized)
        wrappers     [mark, start, end] for the quotation marks text.quote() wrote around one
                     value: the mark's position and the value's interval. A quotation that
                     was cut has no closing wrapper: the cut sign stands before the mark.
                     With call_values, also the two quotation marks this server's JSON put
                     around each string VALUE of the proposed call's arguments (never a key,
                     never a mark escaped inside a value): the same kind of mark around the
                     same kind of thing, in the one piece of a state that is JSON this server
                     serialized. Off unless asked for, and a layout says which it is.

    Taken from the map, never from the look of the text: a quotation mark or a brace inside
    session text, a rule or an unmapped literal is content. Bound to the state by hash; a
    state the map is not of raises."""
    digest = hashlib.sha256(state.encode("utf-8", "surrogatepass")).hexdigest()
    if digest != found.sha256 or len(state) != found.chars:
        raise ValueError("this map is of another state")
    structural, wrappers, segments = [], [], found.segments
    for index, segment in enumerate(segments):
        low, high = segment["at"]
        if (segment["kind"], segment.get("field")) == ("call", "arguments") and segment.get(
            "exact"
        ):
            quoted = escaped = False
            opened, strings = None, []
            for at in range(low, high):
                char = state[at]
                if quoted:
                    quoted, escaped = (
                        not (char == '"' and not escaped),
                        char == "\\" and not escaped,
                    )
                    if not quoted:
                        strings.append((opened, at))
                elif char == '"':
                    quoted, opened = True, at
                elif char in "{}[],":
                    structural.append(at)
            for first, last in strings if call_values else ():
                rest = state[last + 1 : high].lstrip(" ")
                if not rest.startswith(":") and last > first + 1:  # a value, and not an empty one
                    wrappers += [[first, first + 1, last], [last, first + 1, last]]
        if segment.get("role") != "quote" or state[low:high] != '"':
            continue
        after = segments[index + 1 :]
        if (
            after and after[0]["kind"] == SESSION
        ):  # an opening mark: its value runs to the next mark
            end = low + 1
            for piece in after:
                if piece["kind"] != SESSION:
                    break
                end = piece["at"][1]
            closing = next((p for p in after if p["kind"] != SESSION), None)
            wrappers.append([low, low + 1, end])
            if closing and closing.get("role") == "quote" and closing["at"][0] == end:
                wrappers.append([end, low + 1, end])
    told = json.dumps(found.told(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "schema": "kai-span-layout/v1",
        "state_sha256": digest,
        "map_sha256": hashlib.sha256(told.encode("utf-8")).hexdigest(),
        "structural": structural,
        "wrappers": sorted(wrappers),
        "wrapper_sources": ["quote", "call-argument-value"] if call_values else ["quote"],
    }


def main(argv):
    policy = SELECT
    if "--policy" in argv:
        policy = argv[argv.index("--policy") + 1]
        argv = [
            a
            for i, a in enumerate(argv)
            if i not in (argv.index("--policy"), argv.index("--policy") + 1)
        ]
    if len(argv) != 2:
        raise SystemExit(__doc__)
    with open(argv[0], encoding="utf-8") as handle:
        found = SourceMap.read(json.load(handle))
    with open(argv[1], encoding="utf-8") as lines:
        annotations = [json.loads(line) for line in lines if line.strip()]
    for question, told in project(found, annotations, policy).items():
        about = {"question": question, "state_sha256": found.sha256}
        print(json.dumps(about | told, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
