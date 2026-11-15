"""Where annotated evidence stands in a rendered state: span targets by lookup, never by search.

    PYTHONPATH=src/summarizer python training/summarizer/span_targets.py MAP.json ANNOTATIONS.jsonl

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

One line is printed for each question: its spans as half-open code-point intervals of the
state, sorted, or that it is unavailable and why. A question is complete or unavailable,
never partial: one annotation the state does not show whole, or shows twice without being
told which, makes the whole question unavailable, because a head trained on the rest would
be taught that the missing part is not evidence. Overlapping intervals are refused. A map
says whose words an interval is. It does not say the words are true.
"""

import json
import sys

from summarizer.sourcemap import SESSION, SourceMap


def places(found, note):
    """(intervals, None) for one annotation, or (None, why)."""
    kind = note.get("kind")
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
    else:
        return None, "unknown_kind"
    if why:
        return None, why
    if "occurrence" in note:
        chosen = note["occurrence"]
        return ([at[chosen]], None) if 0 <= chosen < len(at) else (None, "no_such_occurrence")
    return (at, None) if len(at) == 1 else (None, "ambiguous_occurrence")


def project(found, annotations):
    """{question: {"complete": True, "spans": [...]} or {"complete": False, "why": [...]}}."""
    out = {}
    for note in annotations:
        told = out.setdefault(note["question"], {"complete": True, "spans": [], "why": []})
        if note.get("empty"):
            told["empty"] = True
            continue
        at, why = places(found, note)
        told["spans"] += at or []
        told["why"] += [why] if why else []
    for told in out.values():
        told["spans"].sort()
        mixed = told.pop("empty", False) and told["spans"]
        crossed = any(a[1] > b[0] for a, b in zip(told["spans"], told["spans"][1:], strict=False))
        told["why"] += ["empty_and_spans"] * bool(mixed) + ["overlapping_spans"] * crossed
        if told["why"]:
            told |= {"complete": False, "spans": None}
        else:
            del told["why"]
    return out


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    with open(argv[0], encoding="utf-8") as handle:
        found = SourceMap.read(json.load(handle))
    with open(argv[1], encoding="utf-8") as lines:
        annotations = [json.loads(line) for line in lines if line.strip()]
    for question, told in project(found, annotations).items():
        about = {"question": question, "state_sha256": found.sha256}
        print(json.dumps(about | told, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
