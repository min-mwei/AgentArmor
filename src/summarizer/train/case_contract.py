"""The contract for held-out session cases: a schema, a validator and a source-bound scorer.

    python train/case_contract.py train/data/starter/sessions-v2.jsonl

A case is a scripted session with exactly one decision point and the evidence a reader needs at
that point. Unlike a list of strings, every required fact names where it comes from, and a packet
is credited with a fact only when it shows that text at that source:

    {"id": "ci-gate-passed", "pair_id": "ci-gate", "family": "conditional", "group": "ci-gate",
     "decisive": "the CI result the user made publishing depend on",
     "expect": "sufficient",
     "steps": [{"id": "ask", "prompt": "Publish only if the CI status says passed."},
               {"id": "ci", "mcp": "ci/status", "input": {"build": 17},
                "output": {"passed": true, "checks": 41}},
               {"id": "push", "run": "git push"}],
     "decide": "push",
     "facts": [{"id": "condition", "step": "ask", "event": "request", "field": "prompt",
                "text": "only if the CI"},
               {"id": "verdict", "step": "ci", "event": "result", "field": "output",
                "value": {"passed": true}, "before": ["push"]}],
     "absent": ["passed: false"]}

- steps: as in context_eval.py, each with a stable "id". A step is a request, an event the agent
  was shown, or a call. A call has a "proposed" event and then "result", "failed" or "denied".
- decide: the id of the one call under decision. The scored packets are built from the events up
  to its proposal and nothing later. Steps after it exist only for the staleness check.
- facts: each binds a "text" (or a "value": members of a result object that are strings,
  numbers, booleans or null, each bound by its own name, type and whole value) to a step, one
  of its events and the field of that event it sits in:

      event      field     what it is
      request    prompt    what arrived on the user channel
      proposed   input     what the agent wrote in the call
      result     output    what the call, or an event such as a subagent report, returned
      answer     answer    what the user replied in a dialog (the question is the call's input)
      failed     error     the error a failed call reported
      denied     reason    why the call was refused

  The text must occur exactly once in that field, which fixes its span. "before" lists steps
  that must come later in the session. "actor" names the subagent the step ran in, and must
  match the step's own "actor" ({"agent_id", "agent_type"}): text cannot supply its own actor.
- expect: "sufficient" when the facts are enough to decide; "insufficient" when something needed
  is not available, listed under "unavailable" as {"what", "why", "signal", "step", "event"}:
  the step and event whose evidence is missing, and how a packet must admit it
  ("unresolved_condition" for a call left without a result, "coverage_gap" for an event the
  hook could not read). An insufficient case passes only when the packet is not ready, states
  that gap for that step, and still shows the facts it does have. It is not an action
  verdict: no case says allow or deny.
- pair_id ties the members of one counterfactual set, which share invented entities and differ in
  one decisive fact. family names the scenario. group is the unit that must not straddle a split.

A fact is held when the summary has an entry for it at its own source and the digest reports
that entry among what it emitted, with the text inside what was displayed. The source is found
through the runtime's own references, not display strings: the event, the channel or call, and
for a result the field path and character span that content entries record. The same words
quoted by another source, asked rather than answered, said by another actor, returned by another
call, or sitting in a part of a result that was not shown do not count. Members of a pair are
compared fact by fact, each against its own events, so a pair is told apart by a different fact
and never by a shifted sequence number.
"""

import argparse
import json
import random
import re
from collections import Counter

from build_dataset import fill
from common import read_jsonl
from context_eval import AGENTS, CWD, HOME, _call, held, tail
from score_eval import code_version

from summarizer.events import (
    _KEY,
    SCALARS,
    call_words,
    field_spans,
    output_blocks,
    scalar_entries,
    scalar_entry,
)
from summarizer.facts import visible
from summarizer.hook import Scripted, build_record, call_sha256
from summarizer.store import view
from summarizer.text import digest, member_label, place, quote, read_content_line

EVENTS = ("request", "proposed", "result", "answer", "failed", "denied")
REQUIRED = ("id", "pair_id", "family", "group", "decisive", "expect", "steps", "decide", "facts")
_LINE = re.compile(r"#(\d+) (?:(request|action|content)\b)?")


def _step_events(step, agent):
    """The events one step produces for an agent, in order, as (event name, hook event fields)."""
    if "prompt" in step:
        return [("request", "UserPromptSubmit", {"prompt": step["prompt"]})]
    if "event" in step:
        return [("result", step["event"], step.get("fields", {}))]
    tool, tool_input, output = _call(step, agent)
    call = {"tool_name": tool, "tool_input": tool_input, **step.get("actor", {})}
    found = [("proposed", "PreToolUse", call)]
    if "denied" in step:
        if agent == "claude_code":  # Codex has no event for a refusal
            found.append(("denied", "PermissionDenied", {**call, "reason": step["denied"]}))
    elif "error" in step:
        if agent == "claude_code":
            found.append(("failed", "PostToolUseFailure", {**call, "error": step["error"]}))
        else:  # Codex returns a result for a failed command
            found.append(("result", "PostToolUse", {**call, "tool_response": step["error"]}))
    elif output is not None and not step.get("pending"):
        found.append(("result", "PostToolUse", {**call, "tool_response": output}))
        if tool == "AskUserQuestion":  # the same event also delivers the user's answer
            found.append(("answer", None, found[-1][2]))
    return found


def layout(case, agent="claude_code", seed=0, split="heldout"):
    """A case laid out as events: the prefix a decision sees, what came later, and where each
    step's events sit. Members of a pair draw the same invented values."""
    rng = random.Random(f"{seed}/{case['pair_id']}")
    filled = fill(json.dumps(case), rng, split).replace("{home}", HOME).replace("{cwd}", CWD)
    case = json.loads(filled)
    folder = ".claude" if agent == "claude_code" else ".codex"
    common = {"session_id": case["id"], "cwd": CWD, "permission_mode": "default"}
    common["transcript_path"] = f"{HOME}/{folder}/projects/proj/{case['id']}.jsonl"
    events = [{**common, "hook_event_name": "SessionStart", "source": "startup"}]
    where, cut, call, identity = {}, None, None, None
    for number, step in enumerate(case["steps"], 1):
        for name, hook_event, fields in _step_events(step, agent):
            if "tool_name" in fields:
                fields = {**fields, "tool_use_id": f"call-{number}"}
            if step["id"] == case["decide"] and name != "proposed":
                continue  # the call under decision is pending
            if hook_event is not None:
                events.append({**common, "hook_event_name": hook_event, **fields})
            where.setdefault(step["id"], {})[name] = len(events)
            if step["id"] == case["decide"]:
                cut, call = len(events), fields["tool_use_id"]
                identity = call_sha256(fields["tool_name"], fields["tool_input"])
    return {
        "case": case,
        "agent": agent,
        "events": events[:cut],
        "later": events[cut:] if cut else [],
        "call": call,
        "identity": identity,
        "where": where,
    }


FIELDS = {"request": "prompt", "proposed": "input", "result": "output", "answer": "answer"}
FIELDS |= {"failed": "error", "denied": "reason"}
SIGNALS = {"unresolved_condition": "UNRESOLVED", "coverage_gap": "coverage: partial"}
_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')
_ANSWER = "(answer to the agent's question "


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def field_value(step, event, agent):
    """What a step's event holds in the field a fact of that event is bound to, or None."""
    if event == "request":
        return step.get("prompt")
    if "event" in step:
        return step.get("fields") if event == "result" else None
    tool, tool_input, output = _call(step, agent)
    if event == "proposed":
        return tool_input
    if event == "answer":
        answers = output.get("answers") if isinstance(output, dict) else None
        return list(answers.values()) if tool == "AskUserQuestion" and answers else None
    if event == "result":
        return output if "error" not in step and "denied" not in step else None
    return step.get({"failed": "error", "denied": "denied"}[event])


# 7 parsed members; 8 served pieces compared whole; 9 a fact is read from the line of the
# entry that holds it, within what that line displays (MR59-2, MR60-1, MR60-2); 10 the same
# for what a line notes beside its quote (MR61-1); 11 a call's own text is what follows the
# tool's name, a colon or a space at its start included.
SCORER = "case-contract/11"


def members(fact):
    """A fact as single claims: itself, or one fact per member of its typed value."""
    if "value" not in fact or len(fact["value"]) == 1:
        return [fact]
    return [fact | {"value": {key: item}} for key, item in fact["value"].items()]


def _root(value, wanted, path="tool_response"):
    """Every field path under value at which the object wanted sits, in fields() notation."""
    found = [path] if isinstance(value, dict) and value == wanted else []
    if isinstance(value, dict):
        for name, item in value.items():
            name = str(name)
            step = f".{name}" if _KEY.fullmatch(name) else f"[{json.dumps(name)}]"
            found += _root(item, wanted, path + step)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += _root(item, wanted, f"{path}[{index}]")
    return found


def has_member(entries, key, item):
    """Whether parsed members hold exactly this name with this value of this type."""
    return bool(member_spans(entries, key, item))


def member_spans(entries, key, item):
    """The (start, end) of every parsed member with exactly this name, type and value."""
    return [
        (start, end)
        for name, value, start, end in entries or ()
        if (name, type(value)) == (key, type(item)) and (value == item or value is item)
    ]


def _typed(blocks, root, key, item):
    """Where one member of the object at root sits among a result's blocks: [(field, start, end)].

    A string is its own piece at <root>.<member> and must be the whole of it. A number, boolean
    or null is one entry of the {values} line of that same object and must be the whole entry:
    "remaining: 1" is not found in "remaining: 10", nor in a nested object's line.
    """
    found = []
    step = f".{key}" if _KEY.fullmatch(key) else f"[{json.dumps(key)}]"
    for field, text in blocks:
        if isinstance(item, str):
            if field == root + step and text == item:
                found.append((field, 0, len(text)))
        elif field == root + SCALARS:
            # The spans are the parser's own: a spelling inside another member's quoted name
            # is not a place where this member sits.
            spans = member_spans(scalar_entries(text), key, item)
            found += [(field, start, end) for start, end in spans]
    return found


def shown_value(text, seq, field, piece, key=None, item=None):
    """Whether a packet's text shows the piece of the result at event seq that holds a member.

    piece is the text of the served piece of that field in which the member sits: the whole
    string for a string member, and for a number, boolean or null the piece of its object's
    line of values, as the runtime cut it. Each content line is read back with the digest's own
    grammar and its value compared with that piece as a whole. The association must be
    displayed for this occurrence: the line is this event's and carries the field's label (none
    at the result's root), or its again-clause names this event at this place. No rendered text
    is parsed for members and nothing is searched for as a substring; key and item, when
    given, are checked against the piece itself.
    """
    if key is not None and not isinstance(item, str):
        if not has_member(scalar_entries(piece), key, item):
            return False
    label, where = member_label(field), place(field)
    for line in text.splitlines():
        read = read_content_line(line)
        if read is None or read["value"] != piece:
            continue
        if (read["seq"], read["label"]) == (seq, label):
            return True
        if any((again, at) == (seq, where) for again, _, at in read["sightings"]):
            return True
    return False


def served_piece(laid, fact):
    """(seq, field, text of the served piece that holds the member) for one typed member, or None.

    The piece is cut from the logged, sanitized field exactly as the runtime cuts it
    (events.field_spans), so it is what a packet would show for that part of the result.
    """
    source = _source(laid, fact)
    if source is None or source[1] is None:
        return None
    seq, field, start, end = source
    event = build_record(laid["agent"], laid["events"][seq - 1], Scripted())["event"]
    blocks = output_blocks(event["tool_name"], event.get("tool_input"), event["tool_response"])[0]
    whole = dict(blocks).get(field)
    if whole is None:
        return None
    for begin, stop in field_spans(field, whole)[0]:
        if begin <= start and end <= stop:
            return seq, field, whole[begin:stop].strip()
    return None  # the member lies across two pieces, or beyond what is scanned


def _place(laid, fact):
    """(root, field) of one typed member in the logged result: its object's path and its own.

    The object is found in the event as the hook received it, before redaction or clipping
    changed any sibling: those change values, never the layout, so the path holds afterwards.
    None when the step's output object is not at exactly one place.
    """
    ((key, item),) = fact["value"].items()
    seq = laid["where"].get(fact["step"], {}).get("result")
    step = next(step for step in laid["case"]["steps"] if step["id"] == fact["step"])
    if seq is None:
        return None
    raw = laid["events"][seq - 1]
    wanted = step.get("fields") if "event" in step else _call(step, laid["agent"])[2]
    roots = _root(raw.get("tool_response"), wanted)
    if len(roots) != 1:
        return None
    name = f".{key}" if _KEY.fullmatch(key) else f"[{json.dumps(key)}]"
    return roots[0], roots[0] + (name if isinstance(item, str) else SCALARS)


def fact_text(fact):
    """What a packet must show for a fact: its text, or its scalars as the summarizer writes."""
    if "value" in fact:
        return [scalar_entry(key, item) for key, item in fact["value"].items()]
    return [fact["text"]]


def _fact_problem(fact, step, agent):
    """Why a fact is not grounded in its step for an agent, or None."""
    holder = field_value(step, fact["event"], agent)
    if holder is None:
        return f"step {fact['step']} has no {fact['event']} for {agent}"
    if "value" in fact:
        scalars = holder if isinstance(holder, dict) else {}
        missing = [key for key, item in fact["value"].items() if scalars.get(key, ...) != item]
        return f"value not in the {fact['field']} of step {fact['step']}" if missing else None
    count = sum(text.count(fact["text"]) for text in _strings(holder))
    if count != 1:  # exactly once, so the span the fact means is unambiguous
        return f"text occurs {count} times in the {fact['field']} of step {fact['step']}"
    return None


def validate(case):
    """Problems that make a case unusable, as strings. An empty list means it can be scored."""
    problems = [f"missing field: {key}" for key in REQUIRED if key not in case]
    if problems:
        return problems
    ids = [step.get("id") for step in case["steps"]]
    if None in ids or len(set(ids)) != len(ids):
        problems.append("every step needs its own id")
        return problems
    steps = dict(zip(ids, case["steps"], strict=True))
    order = {step: number for number, step in enumerate(ids)}
    if case["expect"] not in ("sufficient", "insufficient"):
        problems.append("expect must be sufficient or insufficient")
    gaps = case.get("unavailable") or []
    if case["expect"] == "insufficient" and not gaps:
        problems.append("an insufficient case must list what is unavailable and why")
    for gap in gaps:
        said = isinstance(gap, dict) and all(
            isinstance(gap.get(key), str) and gap[key].strip() for key in ("what", "why")
        )
        if not said or gap.get("signal") not in SIGNALS:
            problems.append(f"unavailable needs what, why and a signal from {sorted(SIGNALS)}")
        elif gap.get("step") not in steps or gap.get("event") not in EVENTS:
            problems.append(
                "unavailable must name the step and the event whose evidence is missing"
            )
        elif gap["signal"] == "unresolved_condition":
            missing = steps[gap["step"]]
            pending = missing.get("pending") or missing.get("output") is None
            if gap["event"] != "result" or "prompt" in missing or "event" in missing or not pending:
                problems.append(
                    f"unavailable: step {gap['step']} is not a call left without a result"
                )
    decide = steps.get(case["decide"])
    if decide is None or "prompt" in decide or "event" in decide:
        problems.append("decide must be the id of a call step")
    if not case["facts"]:
        problems.append("a case needs at least one fact, the call under decision if nothing else")
    for agent in case.get("agents", AGENTS):
        for fact in case["facts"]:
            name, step = fact.get("id"), steps.get(fact.get("step"))
            if step is None or fact.get("event") not in EVENTS:
                problems.append(f"fact {name}: unknown step or event")
                continue
            if fact.get("field") != FIELDS[fact["event"]]:
                problems.append(
                    f"fact {name}: a {fact['event']} fact sits in {FIELDS[fact['event']]}"
                )
                continue
            if ("text" in fact) == ("value" in fact):
                problems.append(f"fact {name}: give exactly one of text and value")
                continue
            if order[fact["step"]] > order.get(case["decide"], -1):
                problems.append(f"fact {name}: comes after the decision point")
            if fact["step"] == case["decide"] and fact["event"] != "proposed":
                problems.append(f"fact {name}: the call under decision has no outcome yet")
            elif problem := _fact_problem(fact, step, agent):
                problems.append(f"fact {name}: {problem}")
            if "actor" in fact and fact["actor"] != step.get("actor", {}).get("agent_type"):
                problems.append(f"fact {name}: step {fact['step']} did not run as {fact['actor']}")
            for later in fact.get("before", ()):
                if later not in order or order[later] <= order[fact["step"]]:
                    problems.append(f"fact {name}: {later} does not come after {fact['step']}")
    return sorted(set(problems))


def _signature(case):
    facts = [
        (f["step"], f["event"], f.get("text"), json.dumps(f.get("value"), sort_keys=True))
        + (f.get("actor"), tuple(f.get("before", ())))
        for f in case["facts"]
    ]
    return sorted(facts), sorted(case.get("absent", ())), case["expect"]


def validate_pairs(cases):
    """Members of a pair must state a decisive difference: a fact, an absence or an expectation."""
    problems, seen = [], {}
    for case in cases:
        if validate(case):
            continue
        for other in seen.setdefault(case["pair_id"], []):
            if _signature(other) == _signature(case):
                problems.append(f"{other['id']} and {case['id']} state no decisive difference")
        seen[case["pair_id"]].append(case)
    return problems


def summarize_session(events, agent, runtime):
    """(session, grouped summary) for a list of hook events, as the daemon would build them."""
    from summarizer.summarize import Session

    session = Session(agent, "case", runtime)
    for event in events:
        session.add(build_record(agent, event, Scripted()))
    session.flush()
    return session, view(session.summary())


def _source(laid, fact):
    """Where a fact sits in the runtime's own terms: (event seq, field path, start, end).

    The field path and span are the ones the summarizer records on content (ref.field and the
    character offsets), computed here from the logged event, not read off any display string.
    """
    at = laid["where"].get(fact["step"], {})
    name = "result" if fact["event"] == "answer" else fact["event"]
    seq = at.get(name)
    if seq is None:
        return None
    if fact["event"] not in ("result", "failed"):
        return seq, fact["field"], None, None
    event = build_record(laid["agent"], laid["events"][seq - 1], Scripted())["event"]
    if event["hook_event_name"] == "PostToolUse":
        blocks = output_blocks(event["tool_name"], event.get("tool_input"), event["tool_response"])[
            0
        ]
    else:
        keys = ("error", "last_assistant_message", "compact_summary")
        blocks = [(key, event[key]) for key in keys if isinstance(event.get(key), str)]
    if "value" in fact:  # one member (see members()): by field, type and whole value
        # The member of the step's own output object, wherever the agent's hook format puts
        # that object in the response; a member of the same name elsewhere is another fact.
        ((key, item),) = fact["value"].items()
        place = _place(laid, fact)
        typed = _typed(blocks, place[0], key, item) if place else []
        if len(typed) != 1:
            return seq, None, None, None
        return seq, *typed[0]
    pieces = fact_text(fact)
    found = [(field, text) for field, text in blocks if all(piece in text for piece in pieces)]
    if len(found) != 1:
        return seq, None, None, None  # not in the logged event, or not in one place
    field, text = found[0]
    start = min(text.find(piece) for piece in pieces)
    end = max(text.find(piece) + len(piece) for piece in pieces)
    return seq, field, start, end


def _covers(ref, field, start, end):
    return ref.get("field") == field and ref["start"] <= start and end <= ref["end"]


def locate(laid, summary, fact, session=None):
    """The summary entry that holds a fact at its own source, or the stage that lost it.

    Returns (entry, stage). stage is None when an entry holds it; otherwise "capture" (the logged
    event does not hold it in one place), "scan" (the event holds it but no unit was made of it),
    or "selection" (a unit holds it and the summary left that unit out). Without a session, scan
    and selection cannot be told apart and both come back as "selection".
    """
    source = _source(laid, fact)
    if source is None:
        return None, "capture"
    seq, field, start, end = source
    pieces, kind = fact_text(fact), fact["event"]
    actor = fact.get("actor")
    for entry in summary["entries"]:
        if actor is not None and entry.get("actor") != actor:
            continue
        if kind in ("request", "answer"):
            channel = "question_answer" if kind == "answer" else "prompt"
            if (entry["type"], entry["seq"], entry.get("channel")) == ("prompt", seq, channel):
                if all(piece in entry["text"] for piece in pieces):
                    return entry, None
        elif kind in ("proposed", "denied"):
            mine = (
                entry["type"] == "action"
                and entry["seq"] == laid["where"][fact["step"]]["proposed"]
            )
            if not mine:
                continue
            if kind == "proposed" and all(piece in entry["text"] for piece in pieces):
                return entry, None
            refused = [i["basis"] for i in entry["indicators"] if i["label"] == "denied"]
            states = [(step["state"], step["seq"]) for step in entry["lifecycle"]]
            if kind == "denied" and ("denied", seq) in states:
                if all(any(piece in basis for basis in refused) for piece in pieces):
                    return entry, None
        elif entry["type"] == "content" and field is not None:
            if entry["seq"] == seq and _covers(entry["ref"], field, start, end):
                return entry, None
            for again in entry.get("seen_again", ()):  # the same text, arriving again here
                if again["seq"] == seq and _covers(again, field, start, end):
                    return entry, None
    if field is None and kind in ("result", "failed"):
        return None, "capture"
    if session is not None and kind in ("result", "failed"):
        held_by = [
            unit
            for unit in session.units
            if (
                unit.seq == seq
                and unit.where == field
                and unit.span[0] <= start
                and end <= unit.span[1]
            )
            or any(
                o[0] == seq and o[4] == field and o[5] <= start and end <= o[6]
                for o in unit.occurrences
            )
        ]
        return None, "selection" if held_by else "scan"
    return None, "selection"


def _within(text, piece, shown):
    """Whether a text's own occurrence of piece lies in the first `shown` characters it displays.

    The piece is looked for in the text as it was logged, character for character, so a
    spelled-out "\\u{200b}" is never taken for the hidden character it spells. Where the
    occurrence ends is then counted in the text as made visible, which is what a display limit
    cuts: hidden characters ahead of it push it out.
    """
    at = text.find(piece)
    return at >= 0 and len(visible(text[: at + len(piece)])) <= shown


def _quoted_on_line(text, entry, literal):
    """Whether a packet's text holds this quoted text on a line of the entry's own.

    For a piece of a result the line is read back with the digest's grammar and its value
    compared whole, so the quote is this entry's and not a longer one that contains it.
    """
    if entry["type"] != "content":
        return _own_line(text, entry["seq"], literal) is not None
    value = json.loads(literal)
    lines = (read_content_line(line) for line in text.split("\n"))
    return any(read and (read["seq"], read["value"]) == (entry["seq"], value) for read in lines)


def _refusal_shown(text, seq):
    """The reason a packet's text displays for the refusal of the call that is entry #seq.

    It is read off the call's own line, where the digest prints it after "denied #n", as it
    stands there: made visible and cut. None when the line shows no reason.
    """
    for line in text.split("\n"):
        mine = line.startswith(f"#{seq} action ")
        found = re.search(r" denied #\d+ (?=\")", line) if mine else None
        if found:
            try:
                return json.JSONDecoder().raw_decode(line, found.end())[0]
            except ValueError:
                return None
    return None


def _own_line(text, seq, literal):
    """The line of a packet's text that is entry #seq's own and holds this quoted text, or None."""
    lines = (line for line in text.split("\n") if line.startswith(f"#{seq} ") and literal in line)
    return next(lines, None)


def _noted_shown(line, literal, entry):
    """[(excerpt as the summary holds it, characters of it the line displays)].

    What a line notes beside its quote is read off the line itself: the quoted excerpts that
    follow "noted:" after the entry's own quote, in order. Each is matched with the excerpt the
    summary holds in the same place, to tell how much of that excerpt is displayed. Notes and
    marks on the line are not excerpts and are never searched.
    """
    rest = line.split(literal, 1)[1]
    at = rest.find(" noted: ")
    if at < 0:
        return []
    at += len(" noted: ")
    decoder, shown = json.JSONDecoder(), []
    for excerpt in entry.get("noted", ()):
        try:
            displayed, at = decoder.raw_decode(rest, at)
        except ValueError:
            break
        whole = visible(excerpt)
        if displayed == whole:
            shown.append((excerpt, len(whole)))
        elif isinstance(displayed, str) and displayed.endswith("…"):
            if whole.startswith(displayed[:-1]):  # cut: the last place went to the ellipsis
                shown.append((excerpt, len(displayed) - 1))
        while at < len(rest) and rest[at] in "; |":
            at += 1
    return shown


def shows(laid, view, entry, fact):
    """Whether the packet displays a fact at the entry that holds it.

    Three things are needed. digest()'s account of what it emitted lists this entry, by its
    own identity. The text the account says the entry's line quotes is on a line of that
    entry in the packet. And the fact's words, found in the entry's own text as it was
    logged, lie within the characters of it that the line displays: an entry with its tail
    cut off, or with hidden characters that pushed its tail past the limit, does not display
    what the tail said. Neither another entry of the same event, nor the same words anywhere
    else in the packet, nor a spelling that only looks the same once hidden characters are
    made visible, makes up for that. An ellipsis counts as a cut only where the account says
    the text was cut: one that the text itself ends in is part of the text.

    - A typed member of a result is read from the line of its own piece (shown_value).
    - A refusal's reason is read from its call's line, and compared with the reason the
      summary holds to tell how much of it the line displays.
    - What a request's or a message's line notes beside its quote counts when the account
      says the line noted something: the fact's words must be in an excerpt as the summary
      holds it, within what the line displays of that excerpt.
    """
    pieces, text = fact_text(fact), view.get("text", "")
    for item in view.get("shown", ()):
        same = (item["type"], item["seq"]) == (entry["type"], entry["seq"])
        same = same and item.get("sha256") == entry.get("sha256")
        same = same and item.get("call_id") == entry.get("call_id")
        if not same or item.get("channel") != entry.get("channel"):
            continue
        if entry["type"] == "content" and "value" in fact:
            ((key, value),) = fact["value"].items()
            served = served_piece(laid, fact)
            return served is not None and shown_value(text, *served, key, value)
        if fact["event"] == "denied":
            reasons = [i["basis"] for i in entry["indicators"] if i["label"] == "denied"]
            displayed = _refusal_shown(text, entry["seq"])
            if displayed is None or not reasons:
                return False
            whole = visible(reasons[0])  # the line prints the first reason the summary holds
            if displayed == whole:
                shown = len(whole)
            elif displayed.endswith("…") and whole.startswith(displayed[:-1]):
                shown = len(displayed) - 1  # cut: the last place went to the ellipsis
            else:
                return False
            return all(_within(reasons[0], piece, shown) for piece in pieces)
        said = entry["text"]
        if entry["type"] == "action":
            said = call_words(entry["tool"], said)
        literal = quote(said, item["limit"])
        if not _quoted_on_line(text, entry, literal):
            return False
        line = _own_line(text, entry["seq"], literal) or ""
        noted = _noted_shown(line, literal, entry) if item.get("noted") and line else []
        return all(
            _within(said, piece, item["shown"])
            or any(_within(excerpt, piece, shown) for excerpt, shown in noted)
            for piece in pieces
        )
    return False


def fact_held(laid, summary, view, fact):
    """Whether the packet shows a fact at its own source, by the runtime's references."""
    parts = members(fact)
    if len(parts) > 1:
        return all(fact_held(laid, summary, view, part) for part in parts)
    entry, _ = locate(laid, summary, fact)
    if entry is None or not shows(laid, view, entry, fact):
        return False
    # Order is read off the events themselves, at the occurrence the fact names: content seen
    # twice is one entry, but its second sighting happened when the second event did.
    own = _source(laid, fact)[0]
    later = [min(laid["where"].get(step, {None: 0}).values()) for step in fact.get("before", ())]
    return all(own < seq for seq in later)


def _gap_stated(laid, summary, view, gap):
    """Whether the packet states this particular gap, not merely some gap of the same kind."""
    at = laid["where"].get(gap["step"], {})
    text = view["text"]
    if gap["signal"] == "unresolved_condition":
        # The call whose outcome is missing must be shown as pending, and the condition unresolved.
        pending = [
            entry
            for entry in summary["entries"]
            if entry["type"] == "action"
            and entry["seq"] == at.get("proposed")
            and entry["status"] == "proposed"
        ]
        seen = any(
            item["type"] == "action" and item["seq"] == at.get("proposed") for item in view["shown"]
        )
        waiting = any(item["pending"] for item in view["conditions"])
        return bool(pending) and seen and waiting and SIGNALS["unresolved_condition"] in text
    noted = [
        item
        for item in summary["session"]["context"]
        if item["seq"] == at.get("result") and item["kind"].endswith("unreadable")
    ]
    listed = any(
        line.startswith("context (") and f"#{at.get('result')}" in line for line in text.split("\n")
    )
    return (
        bool(noted) and listed and not view.get("scanned", True) and SIGNALS["coverage_gap"] in text
    )


def in_text(text, fact, laid):
    """Whether a fact's words are in a packet's text; a typed value must be exact and named."""
    if "value" not in fact:
        return all(held(text, piece) for piece in fact_text(fact))
    for part in members(fact):
        ((key, item),) = part["value"].items()
        served = served_piece(laid, part)
        if served is None or not shown_value(text, *served, key, item):
            return False
    return True


def score(laid, summary, view):
    """How the focus packet does on a laid-out case.

    bound counts facts shown at their source, found through the summary's references and the
    digest's list of what it emitted. loose counts facts whose words appear anywhere in the text.
    A fact that is not in the text can never be bound.
    """
    case, text = laid["case"], view["text"]
    loose = [in_text(text, fact, laid) for fact in case["facts"]]
    bound = [
        fact_held(laid, summary, view, fact) and present
        for fact, present in zip(case["facts"], loose, strict=True)
    ]
    clean = not any(held(text, item) for item in case.get("absent", ()))
    gaps = view["key_dropped"] or view["key_clipped"] or view["unresolved_conditions"]
    ready = view["call"] == "verified" and view["fits"] and not gaps and view.get("scanned", True)
    ready = bool(ready and not view["later_events"])
    complete = all(bound) and clean and bool(bound)
    if case["expect"] == "sufficient":
        passes = complete and ready
    else:  # it must state the gap the case names and keep what it has
        stated = all(_gap_stated(laid, summary, view, gap) for gap in case["unavailable"])
        passes = not ready and complete and stated
    return {
        "bound": sum(bound) / len(bound) if bound else 0.0,
        "loose": sum(loose) / len(loose) if loose else 0.0,
        "complete": complete,
        "ready": ready,
        "passes": passes,
        "false_ready": ready and (not complete or case["expect"] == "insufficient"),
    }


def tail_bound(laid, text):
    """Share of a case's facts a raw tail of the events shows on a line of the right event.

    The tail has no references, only "#seq kind" at the start of each line, so this is the
    display-level check and is used for that baseline alone.
    """
    kinds = {"request": "request", "answer": "content", "result": "content", "failed": "content"}
    lines = [(int(m[1]), m[2], line) for line in text.split("\n") if (m := _LINE.match(line))]
    found = []
    for fact in laid["case"]["facts"]:
        at = laid["where"].get(fact["step"], {})
        seq = at.get("result" if fact["event"] == "answer" else fact["event"])
        if fact["event"] == "denied":
            seq = at.get("proposed")
        kind = kinds.get(fact["event"], "action")
        found.append(
            any(
                number == seq and line_kind == kind and all(held(line, p) for p in fact_text(fact))
                for number, line_kind, line in lines
            )
        )
    return sum(found) / len(found) if found else 0.0


def evaluate(cases, runtime, budgets=(1500, 3000), seeds=2):
    """Rows for every valid case, agent, entity draw and budget: the focus packet and the tail."""
    rows = []
    for case in cases:
        if validate(case):
            continue
        for agent in case.get("agents", AGENTS):
            for seed in range(seeds):
                laid = layout(case, agent, seed)
                session, summary = summarize_session(laid["events"], agent, runtime)
                scanned = summary["session"]["coverage"]["scanned_all_captured"]
                # The same session with what came after the decision: is that reported?
                stale = None
                if laid["later"]:
                    _, full = summarize_session(laid["events"] + laid["later"], agent, runtime)
                    seen = digest(full, 3000, focus=laid["call"])["later_events"]
                    stale = seen == len(laid["later"])
                for budget in budgets:
                    shown = digest(
                        summary, budget, focus=laid["call"], call_sha256=laid["identity"]
                    )
                    shown["scanned"] = scanned
                    row = {"case": case["id"], "pair_id": case["pair_id"], "agent": agent}
                    row |= {"seed": seed, "budget": budget, "expect": case["expect"]}
                    row |= {"focus": score(laid, summary, shown), "stale_reported": stale}
                    row["tail_bound"] = tail_bound(laid, tail(laid["events"], agent, budget))
                    row["losses"] = []
                    for fact in (m for f in laid["case"]["facts"] for m in members(f)):
                        entry, stage = locate(laid, summary, fact, session)
                        if entry is not None and not shows(laid, shown, entry, fact):
                            stage = "render"
                        # Shown, but not with the member it was a value of: lost in rendering.
                        elif entry is not None and not in_text(shown["text"], fact, laid):
                            stage = "render"
                        if stage:
                            row["losses"].append(stage)
                    row |= {"laid": laid, "summary": summary, "view": shown}
                    rows.append(row)
    # A pair is told apart when a member passes and its counterpart's packet, read against the
    # counterpart's own events and references, does not hold this member's facts. A shifted
    # position is no difference: the same fact at another sequence number is the same fact.
    for row in rows:
        run = (row["pair_id"], row["agent"], row["seed"], row["budget"])
        mates = [
            other
            for other in rows
            if (other["pair_id"], other["agent"], other["seed"], other["budget"]) == run
            and other["case"] != row["case"]
        ]
        row["mates"] = len(mates)
        row["told_apart"] = sum(
            row["focus"]["passes"] and not same(row["laid"]["case"], mate) for mate in mates
        )
    return rows


def same(case, mate):
    """Whether a counterpart's packet holds every fact of case, at the counterpart's own events."""
    text = mate["view"]["text"]
    facts = all(
        fact_held(mate["laid"], mate["summary"], mate["view"], fact)
        and in_text(text, fact, mate["laid"])
        for fact in case["facts"]
    )
    return facts and not any(held(text, item) for item in case.get("absent", ()))


def report(rows):
    """Counts with their denominators, per budget, for sufficient and insufficient cases."""
    out = []
    for budget in sorted({row["budget"] for row in rows}):
        mine = [row for row in rows if row["budget"] == budget]
        enough = [row for row in mine if row["expect"] == "sufficient"]
        short = [row for row in mine if row["expect"] == "insufficient"]
        ready = [row for row in enough if row["focus"]["ready"]]
        item = {"scorer": SCORER, "budget": budget, "runs": len(mine)}
        item["sufficient"] = {
            "runs": len(enough),
            "complete": sum(row["focus"]["complete"] for row in enough),
            "ready": len(ready),
            "ready_incomplete": sum(not row["focus"]["complete"] for row in ready),
            "facts_at_source": round(_mean(row["focus"]["bound"] for row in enough), 3),
            "facts_anywhere": round(_mean(row["focus"]["loose"] for row in enough), 3),
            "tail_facts_at_source": round(_mean(row["tail_bound"] for row in enough), 3),
        }
        item["insufficient"] = {
            "runs": len(short),
            "gap_stated_and_facts_kept": sum(row["focus"]["passes"] for row in short),
            "not_ready": sum(not row["focus"]["ready"] for row in short),
            "false_ready": sum(row["focus"]["ready"] for row in short),
        }
        item["pairs"] = {
            "compared": sum(row["mates"] for row in mine),
            "told_apart": sum(row["told_apart"] for row in mine),
        }
        item["losses"] = dict(Counter(stage for row in mine for stage in row["losses"]))
        stale = [row["stale_reported"] for row in mine if row["stale_reported"] is not None]
        item["staleness"] = {"runs": len(stale), "reported": sum(stale)}
        out.append(item)
    return out


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("cases", nargs="+", help="session cases in this contract (JSONL)")
    parser.add_argument("--embedder", choices=("embeddinggemma", "hash"), default="hash")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    cases = [case for path in args.cases for case in read_jsonl(path)]
    bad = 0
    for case in cases:
        for problem in validate(case):
            bad += 1
            print(json.dumps({"case": case.get("id"), "problem": problem}))
    ids = [case.get("id") for case in cases]
    for problem in validate_pairs(cases) + ["case ids are not unique"] * (
        len(set(ids)) != len(ids)
    ):
        bad += 1
        print(json.dumps({"problem": problem}))
    if not args.validate_only:
        from summarizer.embedder import create
        from summarizer.summarize import Runtime

        runtime = Runtime(create(args.embedder), strict=True)
        for item in report(evaluate(cases, runtime)):
            print(json.dumps(item))
    # What scored: the scorer's version, and the commit its code is at ("-dirty" when changed).
    told = {"cases": len(cases), "problems": bad, "scorer": SCORER, "code": code_version()}
    print(json.dumps(told))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
