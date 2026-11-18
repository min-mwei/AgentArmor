"""What a decision packet shows of each session, claim by claim. Standard library only.

A denial that rests on an absence ("the user never granted this", "the check the user named was
never read") is supported by a packet only where every place that could hold the missing thing is
shown, and shown in full. A packet that leaves out the middle of a long tool result can still
support such a premise: tool output cannot grant authority. One that leaves out a user request,
cuts one, or leaves out part of the very result a condition or a request points at cannot (MR414).

This module writes that accounting as a few neutral lines in the packet itself, on every packet
whatever it is later labelled, from the session's own inventory of its events (the summary's
entries, its counts of what it did not keep and its catalogue of calls not kept) and from what
the text shows (the digest's "shown" list and the history section's items). It is versioned
(VERSION) and read back by read(), so a training reader and a monitor take the same lines the
model reads. premise() says whether the lines support one kind of absence premise. Nothing here
says a call may be allowed, and nothing says whether a premise is true: only whether the text
shows everything that could make it false.

The whole-packet record of what was left out (the NOT SHOWN line, the history section's second
line) and the account of lower-trust sources are separate and unchanged.

    COVERAGE agentarmor-coverage/1 (through the call under decision)
    this session: call whole; user inputs 3, all whole; actions 5, all shown; results of calls
      #3 whole, #5 2/7, #9 none   (one line in a packet)
    [s:ab12]: user inputs 2: 1 whole, not shown #4; actions 6: 1 shown; results of calls #8 whole
    sessions: 1 earlier searched; links resolved

A user input is a prompt or an answer to the agent's question. "whole" is every character the
session delivered; "cut", "redacted" and "not shown" name the input by its event; "not kept" counts
inputs the summary itself did not keep, whose events are not known. An action is a call before the
call under decision. Results are listed for every call the text shows, in session order: "whole",
"k/n" pieces shown, "not shown", "none" (no result observed), "empty" (returned nothing) or "not
known" (the summary cannot say how many pieces came back).
"""

import re

VERSION = "agentarmor-coverage/1"
TITLE = f"COVERAGE {VERSION} (through the call under decision)"
LISTED = 8  # event numbers listed in one clause before the rest are counted
THIS = "this session"
EARLIER = "earlier sessions"
CAP = 1000  # characters of the block at most; over it, detail is collapsed into counts
COLLAPSED = "detail collapsed to fit the block's cap"
REDACTED = "[REDACTED:"


def _ids(seqs):
    seqs = sorted(seqs)
    shown = " ".join(f"#{seq}" for seq in seqs[:LISTED])
    return shown + (f" +{len(seqs) - LISTED} more" if len(seqs) > LISTED else "")


def _tied(shown):
    """{piece sha256: calls whose result the text shows it as}, as text._conditions reads it."""
    tied = {}
    for item in shown:
        if item.get("type") == "content":
            calls = {item.get("call_id"), *(a.get("call_id") for a in item.get("seen_again", ()))}
            tied.setdefault(item.get("sha256"), set()).update(calls - {None, ""})
    return tied


def _result(entry, tied):
    """How much of one call's result the text shows, in the words of the results clause."""
    status = entry.get("status")
    ids = list(entry.get("results") or ())
    count = max(
        len(ids), int(entry.get("result_chunks") or 0), int(entry.get("result_pieces") or 0)
    )
    if not count:
        if "result_pieces" in entry:
            return "empty"
        return "none" if status in (None, "proposed") else "not known"
    seen = sum(1 for sha in ids if entry.get("call_id") in tied.get(sha, ()))
    if seen == count:
        return "whole"
    return f"{seen}/{count}" if seen else "not shown"


def _session(summary, shown, until=None, call=None):
    """The account of one session: what it held through `until` (an event number of its own, or
    everything for another session already cut at the call's time) and what of it is shown."""
    entries = [e for e in summary["entries"] if until is None or e["seq"] < until]
    session = summary["session"]
    told = {("prompt", item["seq"]): item for item in shown if item.get("type") == "prompt"}
    acted = {item["seq"]: item for item in shown if item.get("type") == "action"}
    inputs = {"total": 0, "whole": 0, "cut": [], "redacted": [], "not_shown": [], "not_kept": 0}
    for entry in (e for e in entries if e["type"] == "prompt"):
        inputs["total"] += 1
        item = told.get(("prompt", entry["seq"]))
        if item is None:
            inputs["not_shown"].append(entry["seq"])
        elif REDACTED in str(entry.get("text", "")):
            inputs["redacted"].append(entry["seq"])
        elif item["shown"] < item["of"]:
            inputs["cut"].append(entry["seq"])
        else:
            inputs["whole"] += 1
    kept_out = int((session.get("omitted") or {}).get("prompts") or 0)
    inputs["not_kept"] = kept_out
    inputs["total"] += kept_out
    actions = {"total": 0, "shown": 0, "cut": [], "not_shown": [], "unlisted": 0}
    results, tied = [], _tied(shown)
    for entry in (e for e in entries if e["type"] == "action" and e is not call):
        actions["total"] += 1
        item = acted.get(entry["seq"])
        if item is None:
            actions["not_shown"].append(entry["seq"])
            continue
        actions["shown"] += 1
        if item["shown"] < item["of"]:
            actions["cut"].append(entry["seq"])
        results.append((entry["seq"], _result(entry, tied)))
    for dropped in session.get("dropped_calls") or ():
        if until is None or (isinstance(dropped.get("seq"), int) and dropped["seq"] < until):
            actions["total"] += 1
            actions["not_shown"].append(dropped["seq"])
    unlisted = int(session.get("dropped_calls_not_listed") or 0)
    actions["unlisted"] = unlisted
    actions["total"] += unlisted
    flags = []
    if (session.get("coverage") or {}).get("gaps"):
        flags.append("capture partial")
    as_of = summary.get("as_of") or {}
    if as_of.get("unknown"):
        flags.append("times not all known")
    if as_of.get("tied"):
        flags.append("cutoff tied")
    return {"inputs": inputs, "actions": actions, "results": results, "flags": flags}


def account(mine, call, shown, others=(), set_aside=0, unread=None, unbound=0):
    """The coverage of one packet.

    mine is the deciding session's summary, call its entry for the call under decision (or None),
    shown the digest's "shown" list. others is [(alias, summary cut at the call's time, items the
    history section shows of it, unresolved reason or None)] for every other session the lookup
    searched, oldest first. set_aside counts sessions the lookup proved to be another project,
    unread those that might bear on the call and could not be read (None when the caller does not
    say), unbound the session names in the user's statements that bound to no session.
    """
    until = call["seq"] if call is not None else None
    own = _session(mine, shown, until, call)
    own["name"] = THIS
    if call is not None:
        focus = next(
            (i for i in shown if i.get("type") == "action" and i["seq"] == call["seq"]), None
        )
        own["call"] = (
            "not shown" if focus is None else "whole" if focus["shown"] >= focus["of"] else "cut"
        )
    sessions = [own]
    unresolved = []
    for alias, summary, items, reason in others:
        one = _session(summary, items)
        one["name"] = f"[s:{alias}]"
        if reason:
            unresolved.append(one["name"])
        sessions.append(one)
    links = {"searched": len(sessions) - 1, "set_aside": set_aside, "unresolved": unresolved}
    links |= {"unread": unread, "unbound": unbound}
    return {"version": VERSION, "sessions": sessions, "links": links}


def _inputs_clause(inputs):
    if inputs["whole"] == inputs["total"]:
        return f"user inputs {inputs['total']}, all whole"
    parts = [f"{inputs['whole']} whole"]
    for key, word in (("cut", "cut"), ("redacted", "redacted"), ("not_shown", "not shown")):
        if inputs[key]:
            parts.append(f"{word} {_ids(inputs[key])}")
    if inputs["not_kept"]:
        parts.append(f"not kept {inputs['not_kept']}")
    return f"user inputs {inputs['total']}: " + ", ".join(parts)


def _actions_clause(actions, listed):
    if actions["shown"] == actions["total"] and not actions["cut"]:
        return f"actions {actions['total']}, all shown"
    parts = [f"{actions['shown']} shown"]
    if actions["cut"]:
        parts.append(f"cut {_ids(actions['cut'])}")
    hidden = len(actions["not_shown"]) + actions["unlisted"]
    if hidden and listed:
        named = _ids(actions["not_shown"]) if actions["not_shown"] else ""
        more = (
            f"{' ' if named else ''}+{actions['unlisted']} not listed"
            if actions["unlisted"]
            else ""
        )
        parts.append(f"not shown {named}{more}")
    return f"actions {actions['total']}: " + ", ".join(parts)


def _session_line(one, collapse=False):
    clauses = []
    if "call" in one:
        clauses.append(f"call {one['call']}")
    clauses.append(_inputs_clause(one["inputs"]))
    clauses.append(_actions_clause(one["actions"], listed=one["name"] == THIS))
    if one["results"] and not collapse:
        said = ", ".join(f"#{seq} {word}" for seq, word in one["results"])
        clauses.append(f"results of calls {said}")
    elif one["results"]:
        counts = {}
        for _, word in one["results"]:
            kind = "partial" if "/" in word else word
            counts[kind] = counts.get(kind, 0) + 1
        said = ", ".join(f"{n} {kind}" for kind, n in counts.items())
        clauses.append(f"results of calls {said} ({COLLAPSED})")
    clauses += one["flags"]
    return f"{one['name']}: " + "; ".join(clauses)


def _earlier_line(sessions):
    """Every earlier session in one line, for a block that would pass its cap otherwise."""
    total = sum(one["inputs"]["total"] for one in sessions)
    whole = sum(one["inputs"]["whole"] for one in sessions)
    acted = sum(one["actions"]["total"] for one in sessions)
    shown = sum(one["actions"]["shown"] for one in sessions)
    if whole == total:
        inputs = f"user inputs {total}, all whole"
    else:
        short = [one["name"] for one in sessions if one["inputs"]["whole"] < one["inputs"]["total"]]
        named = " ".join(short[:LISTED]) + (
            f" +{len(short) - LISTED} more" if len(short) > LISTED else ""
        )
        inputs = f"user inputs {total}: {whole} whole, not all whole in {named}"
    flagged = sorted({flag for one in sessions for flag in one["flags"]})
    line = f"{EARLIER}: {len(sessions)} sessions; {inputs}; actions {acted}: {shown} shown"
    return "; ".join([line, *flagged, f"per session {COLLAPSED}"])


def lines(told):
    """The lines a packet carries for this account, title first, at most CAP characters in all.

    Over the cap, each call's result is first told as counts by kind instead of by event, and then
    the earlier sessions are told in one line; each says that it was collapsed. A premise that
    rests on a particular result, or a particular session's inputs, is then not supported by the
    lines (premise()): the reader is told that the detail is not there, never given less as all."""
    links = told["links"]
    line = f"sessions: {links['searched']} earlier searched"
    if links["set_aside"]:
        line += f", {links['set_aside']} set aside as another project"
    gaps = []
    if links["unresolved"]:
        named = links["unresolved"]
        gaps.append(
            "place not known "
            + " ".join(named[:LISTED])
            + (f" +{len(named) - LISTED} more" if len(named) > LISTED else "")
        )
    if links["unread"]:
        gaps.append(f"{links['unread']} could not be read")
    if links["unbound"]:
        gaps.append(f"{links['unbound']} session names bound to none")
    tail = line + "; " + ("; ".join(gaps) if gaps else "links resolved")
    own, others = told["sessions"][0], told["sessions"][1:]
    for collapse, together in ((False, False), (True, False), (True, True)):
        rows = [_session_line(own, collapse)]
        if together and others:
            rows.append(_earlier_line(others))
        else:
            rows += [_session_line(one, collapse) for one in others]
        out = [TITLE, *rows, tail]
        if len("\n".join(out)) <= CAP:
            break
    return out


def placed(text, block, before=None):
    """The text with the block inserted ahead of the line `before` (the history section's
    title), or ahead of its last line (what was left out) when it has no such line: the session's
    own lines, then coverage, then what other sessions show."""
    if before and f"\n{before}\n" in text:
        head, _, rest = text.partition(f"\n{before}\n")
        return "\n".join([head, *block, before, rest])
    body, _, last = text.rpartition("\n")
    return "\n".join([*([body] if body else []), *block, last])


def framed(build, budget, account_of, before=None):
    """A view with its coverage lines (placed()) added to the view built in the whole budget.

    The lines are outside the budget: the view is byte for byte the one build(budget) makes, with
    the block inserted, and "coverage_chars" says how long the block made the text (at most CAP and
    its line breaks). Whoever measures a packet against its budget measures the text less that.
    """
    view = build(budget)
    told = account_of(view)
    text = placed(view["text"], lines(told), before)
    return view | {"text": text, "coverage": told, "coverage_chars": len(text) - len(view["text"])}


def digest(summary, max_chars, focus=None, call_sha256=None):
    """A session's own digest (text.digest()) with its coverage lines: the packet of a call built
    without earlier sessions, whose lines then say that none was searched."""
    from .text import digest as plain

    call = next((e for e in summary["entries"] if focus and e.get("call_id") == focus), None)
    return framed(
        lambda room: plain(summary, room, focus=focus, call_sha256=call_sha256),
        max_chars,
        lambda view: account(summary, call, view["shown"]),
    )


# Reading the lines back: the training reader and the monitor take what the model reads.
_HEAD = re.compile(r"^COVERAGE (agentarmor-coverage/\d+) ")
_NAME = re.compile(r"^(this session|earlier sessions|\[s:[^\]]+\]): (.*)$")
_IDS = re.compile(r"#(\d+)")


def _seqs(text):
    return [int(x) for x in _IDS.findall(text)]


def read(text):
    """The coverage lines of a text as data, or None when it has none.

    {"version", "sessions": {name: {"call", "inputs", "actions", "results", "flags"}},
    "links": {"searched", "set_aside", "resolved", "gaps"}}. A clause that lists "+N more"
    leaves those N unnamed: "more" is set on that clause."""
    rows = text.split("\n")
    start = next((n for n, row in enumerate(rows) if _HEAD.match(row)), None)
    if start is None:
        return None
    out = {"version": _HEAD.match(rows[start])[1], "sessions": {}, "links": None}
    for row in rows[start + 1 :]:
        if row.startswith("sessions: "):
            head, _, rest = row[len("sessions: ") :].partition("; ")
            searched = re.match(r"(\d+) earlier searched", head)
            aside = re.search(r"(\d+) set aside", head)
            out["links"] = {
                "searched": int(searched[1]) if searched else None,
                "set_aside": int(aside[1]) if aside else 0,
                "resolved": rest == "links resolved",
                "gaps": [] if rest == "links resolved" else rest.split("; "),
            }
            break
        found = _NAME.match(row)
        if not found:
            return out | {"malformed": row}
        one = {"call": None, "inputs": None, "actions": None, "results": [], "flags": []}
        for clause in found[2].split("; "):
            if clause.startswith("call "):
                one["call"] = clause[5:]
            elif clause.startswith("user inputs "):
                one["inputs"] = _read_inputs(clause)
            elif clause.startswith("actions "):
                one["actions"] = _read_actions(clause)
            elif clause.startswith("results of calls ") and COLLAPSED in clause:
                one["collapsed"] = True
            elif clause.startswith("results of calls "):
                for piece in clause[len("results of calls ") :].split(", "):
                    seq, _, word = piece.partition(" ")
                    one["results"].append((int(seq.lstrip("#")), word))
            elif COLLAPSED in clause:
                one["collapsed"] = True
            elif found[1] == EARLIER and re.fullmatch(r"\d+ sessions", clause):
                one["sessions"] = int(clause.split()[0])
            else:
                one["flags"].append(clause)
        out["sessions"][found[1]] = one
    return out


def _read_inputs(clause):
    total = int(re.match(r"user inputs (\d+)", clause)[1])
    told = {"total": total, "all_whole": clause.endswith("all whole"), "more": "more" in clause}
    for key, word in (("cut", "cut"), ("redacted", "redacted"), ("not_shown", "not shown")):
        part = re.search(rf"(?:: |, ){word} ((?:#\d+ ?)+(?:\+\d+ more)?)", clause)
        told[key] = _seqs(part[1]) if part else []
    kept = re.search(r"not kept (\d+)", clause)
    told["not_kept"] = int(kept[1]) if kept else 0
    return told


def _read_actions(clause):
    total = int(re.match(r"actions (\d+)", clause)[1])
    told = {"total": total, "all_shown": clause.endswith("all shown")}
    shown = re.search(r": (\d+) shown", clause)
    told["shown"] = total if told["all_shown"] else int(shown[1]) if shown else 0
    cut = re.search(r", cut ((?:#\d+ ?)+)", clause)
    told["cut"] = _seqs(cut[1]) if cut else []
    hidden = re.search(r", not shown (.*)$", clause)
    told["not_shown"] = _seqs(hidden[1]) if hidden else []
    return told


def previous_result(told, name, seq):
    """The event number of the last call before event seq of session name whose result the lines
    list as having come back: what "the previous result" in a request said at seq points into.
    The lines list results only for calls the text shows; a call left out is not among them, and
    an action clause that is not "all shown" then leaves which call came last unknown (None)."""
    one = (told or {}).get("sessions", {}).get(name)
    if not one or not (one["actions"] or {}).get("all_shown"):
        return None
    back = [at for at, word in one["results"] if at < seq and word not in ("none",)]
    return max(back) if back else None


NO_GRANT, MISSING_CHECK, SESSION_BOUNDARY = "no_grant", "missing_check", "session_boundary"


def premise(told, kind, checks=(), material=()):
    """(supported, reasons): whether coverage lines support one absence premise.

    told is read()'s result. kind is NO_GRANT ("no user grant for this action and target"),
    MISSING_CHECK ("no qualifying result of the named check") or SESSION_BOUNDARY ("the grant
    was for an earlier session and nothing renewed it"). checks are the event numbers of this
    session's calls to the named check, as the timeline shows them; material are (session name,
    event number) of results a shown request points into ("use the target in the previous
    result"). Every absence premise needs every user input of every searched session shown
    whole, the call under decision shown whole, the session links resolved and capture whole:
    any of them could hold or change a grant. A missing check also needs every action of this
    session shown, so that "never called" can be told, and each call to the check with its
    result whole, empty or never observed. A result left in part elsewhere does not matter
    unless it is material. Nothing here says the premise is true."""
    why = []
    if told is None:
        return False, ["no coverage lines"]
    if told.get("malformed"):
        return False, ["coverage lines malformed"]
    links = told.get("links")
    if not links:
        why.append("no sessions line")
    elif not links["resolved"]:
        why.append("session links: " + "; ".join(links["gaps"]))
    sessions = told["sessions"]
    if THIS not in sessions:
        why.append("no line for this session")
    for name, one in sessions.items():
        inputs = one["inputs"] or {}
        if not inputs.get("all_whole"):
            why.append(f"{name}: user inputs not all whole")
        if any(
            flag in one["flags"]
            for flag in ("capture partial", "times not all known", "cutoff tied")
        ):
            why.append(f"{name}: " + ", ".join(one["flags"]))
    if THIS in sessions and sessions[THIS]["call"] != "whole":
        why.append("call under decision not shown whole")
    collapsed = {name for name, one in sessions.items() if one.get("collapsed")}
    results = {(name, seq): word for name, one in sessions.items() for seq, word in one["results"]}
    for where in material:
        if where[0] in collapsed:
            results[tuple(where)] = "collapsed"
    if kind == MISSING_CHECK and THIS in collapsed:
        results.update({(THIS, seq): "collapsed" for seq in checks})
    for where in material:
        if results.get(tuple(where)) not in ("whole", "empty"):
            why.append(
                f"material result {where[0]} #{where[1]}: {results.get(tuple(where), 'not shown')}"
            )
    if kind == MISSING_CHECK and THIS in sessions:
        actions = sessions[THIS]["actions"] or {}
        if not actions.get("all_shown"):
            why.append("this session: actions not all shown")
        for seq in checks:
            word = results.get((THIS, seq), "not shown")
            if word not in ("whole", "empty", "none"):
                why.append(f"named check #{seq}: {word}")
    return not why, why
