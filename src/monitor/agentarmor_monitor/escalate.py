"""Decide what to send to Kai, build the packet, and record what came back.

Summarizer tags; it does not judge. Anything it marks at or above a level is a trigger. For
each trigger a packet is built: Summarizer's digest of the session focused on the call the
trigger belongs to, which carries the user's requests, the call, what led to it, where its
operands were first named, reported outcomes, and what is missing. Kai reads that packet and
decides: for a proposed call, allow or deny under the rules; for anything, whether untrusted
content is redirecting the agent. The result is a detection record, appended to the session's
detections.jsonl. Recording a decision does not enforce it; the gate (gate.py) does that.

Every record says how far its evidence can be relied on:

- packet.complete: Summarizer's packet held what it thinks a decision needs, every captured
  event was scanned, and the proposed call's own arguments were shown to Kai whole. An answer
  Kai gave on anything less is recorded but is not a completed check.
- packet.call_source: where the proposed call's text came from. "arguments": the caller held
  the call (the gate). "log": the arguments the hook logged for it, found by call id and by
  the hash the hook took before redaction. "representation": only the text Summarizer shows
  for a call, which is a reading of it (a patch is shown as its files and added lines) and is
  never taken for the whole call.
- review: "pre_action" when the hooks delivered the call's proposal, nothing has been reported
  to have become of it, and the summary holds nothing from after it; else "retrospective".
  Only a pre-action review is evidence about what could be known in time. Whether the call was
  shown whole and whether the evidence is from before it are separate properties.
- summary_embedding: what the summary says embedded it (backend, model, revision, device,
  precision, probes). The same session marked on another device can build another packet, so
  a record names the one its summary came from. It is not part of the request.
- summary_detector: what the summary says marked it (hashes of the wording rules, of the priors
  and levels, and of Summarizer's source). The same session marked under other rules can build
  another packet too. Null for a summary that does not say, and a part of it that the summary
  gives as unknown stays unknown. It is not part of the request either.
"""

import copy
import hashlib
import json
import os
import time

from summarizer.events import call_words
from summarizer.redact import compact
from summarizer.store import EVENTS, read_events
from summarizer.text import digest

from . import policy as _policy
from .kai import (
    DECISION,
    MAX_BYTES,
    XPIA,
    KaiMismatch,
    KaiRejected,
    KaiUnavailable,
    KaiUnsent,
    request,
    wire_bytes,
)
from .policy import layout, load_rules, packet_room, state, writable
from .route import chosen_for

SCHEMA = "agentarmor-detection/v1"
DETECTIONS = "detections.jsonl"
LEVELS = {"low": 1, "medium": 2, "high": 3}
FLAG_AT = 0.5  # where the redirection answer counts as flagged. Not a qualified cutoff.
MIN_CHARS = 1000  # the smallest packet tried when the service refuses a larger one


def key_of(entry):
    """What identifies a trigger across summary updates."""
    return f"{entry['type']}:{entry['seq']}:{entry.get('sha256') or entry.get('call_id') or ''}"


def triggers(summary, minimum="low"):
    """Entries Summarizer marked at or above minimum, oldest first: actions, content, messages."""
    floor = LEVELS[minimum]
    found = [
        entry
        for entry in summary["entries"]
        if entry["type"] in ("action", "content", "message")
        and LEVELS.get(entry.get("level"), 0) >= floor
    ]
    return sorted(found, key=lambda entry: entry["seq"])


def packet(summary, entry, max_chars, sessions=None):
    """(text, account, entries shown): Summarizer's digest for the call a trigger belongs to.

    The account is Summarizer's own, of what the text holds and what found no room. The
    entries are the ones the text shows, as digest() reports them: no text, only what each is.

    sessions, when given, is what other sessions hold for the call ("on" mode of
    --earlier-sessions): {"mine", "others", "counts", "home"} as earlier.others_of() makes
    them. The packet is then Summarizer's lookup (summarizer.history.packet): this session's
    digest and, in the same budget, an EARLIER SESSIONS section, with the lookup's own account
    under "history". Such a packet is ready only if the history is complete by that account
    and every other session that might bear on the call was read up to date (earlier.scope): a
    section that left something out, or sessions nobody could read, are no evidence that nothing
    more bears on the call.
    """
    focus = entry.get("call_id") or None
    if sessions is None:
        shown = digest(summary, max_chars, focus=focus, call_sha256=entry.get("call_sha256"))
    else:
        from summarizer import history

        shown = history.packet(
            sessions["mine"],
            focus,
            sessions["others"],
            sessions["home"],
            max_chars,
            entry.get("call_sha256"),
        )
    coverage = summary["session"]["coverage"]
    gaps = shown["key_dropped"] or shown["key_clipped"] or shown["unresolved_conditions"]
    # The text is measured here, and its own flag is not taken on trust (MR311 P1): a packet
    # longer than its room does not fit, whatever it says, and is not ready.
    over_budget = len(shown["text"]) > max_chars
    fits = bool(shown["fits"] and not over_budget)
    ready = bool(fits and not gaps and coverage["scanned_all_captured"])
    lookup = None
    if sessions is not None:
        account = shown.get("history") or {}
        counts = sessions["counts"]
        lookup = {"complete": account.get("complete", True), "why": account.get("why", [])}
        # The one coverage rule of routing and readiness (earlier.scope, MR171): a session is
        # set aside only when the search index proves it cannot bear on the call.
        known = counts.get("unknown", 1) == 0 and "error" not in counts
        lookup |= {"coverage_known": known, "sessions": counts}
        lookup |= {
            "sources": len(account.get("sources", ())),
            "lines": len(account.get("items", ())),
        }
        lookup |= {"route": shown.get("route"), "route_reason": shown.get("route_reason")}
        # What came before the current request and bears on the call (history.prior_events).
        lookup |= {"prior": shown.get("prior", [])}
        ready = ready and lookup["complete"] and lookup["coverage_known"]
    # A call under decision must be the logged one. Content has no call of its own to verify:
    # the call it came back from is only where the packet is centred.
    ready = ready and (entry["type"] != "action" or shown["call"] == "verified")
    text = shown["text"]
    about = {
        "chars": len(text),
        "max_chars": max_chars,
        "sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
        "call": shown["call"],
        "fits": fits,
        "over_budget": over_budget,
        "dropped": shown["dropped"],  # lines that found no room, by section
        "key_dropped": shown["key_dropped"],
        # Lines that quote less of their entry than there is, by kind, and those among them
        # that are key evidence. The second keeps a packet from being ready; the first is
        # every cut there is, so that none is left to be found by reading the text.
        "cut": shown["cut"],
        "key_clipped": shown["key_clipped"],
        "unresolved_conditions": shown["unresolved_conditions"],
        "later_events": shown["later_events"],
        "scanned_all_captured": coverage["scanned_all_captured"],
        # Whether the packet holds what a decision needs, by Summarizer's own account.
        "ready": ready,
    }
    if lookup is not None:
        about["history"] = lookup
    return text, about, shown["shown"]


def source_map(rules, summary, entry, max_chars, sessions, call, body, observed=None):
    """The map of one state to its sources (summarizer.sourcemap), or None.

    For the state `body` as detect() built it from these same arguments: which characters
    are a session's (and whose, with the trust Summarizer gave them), which a rule's, a host
    fact's, the call's, and which this server wrote. The packet is rendered once more under
    trace; a map is returned only if that gives the very state that was sent. It is local:
    it holds no text, it is not sent to any model, and it says who wrote an interval, never
    that what the interval claims is so.
    """
    from summarizer import sourcemap

    text = packet(summary, entry, max_chars, sessions)[0]
    found = sourcemap.map_of(
        lambda: packet(summary, entry, max_chars, sessions)[0],
        plain=text,
        cutoff={"session": summary["session"].get("session_id"), "seq": entry["seq"]},
        frame=lambda shown: layout(rules, shown, call, observed),
    )
    same = found is not None and found.sha256 == hashlib.sha256(body.encode()).hexdigest()
    return found if same else None


def spans_record(told, mapped, state=None):
    """What a record keeps of a reply's evidence, question by question (kai.evidence()).

    A question answered with spans has each resolved through the state's own map: the
    interval, and what it is an interval of (session, event, field, trust and source
    interval; a string Summarizer derived from an event, as that; a rule; a host fact; the
    call; this server's own words). No text is kept. An interval is evidence only if every
    character of it has a known source from before the call (sourcemap.order): one that
    touches unmapped session text, an event after the call under decision in its own
    session, an event of another session that is later than the call's time or cannot be
    placed against it, or a state that has no map, makes the whole question an "error" with
    its reason, and none of its spans is kept. An error is not an empty answer, and neither
    changes the decision.

    Beside the intervals the record keeps what they were resolved through ("map": the map
    by hash, the state it is of, the call it places this session's events against and the
    time each other session was cut at), so that the order of every source can be checked
    again from the record alone.

    The intervals kept as "spans" are strict: as the service decoded them ("strict" says
    what kind). Where the package said what its head's targets were projected by, and state
    (the text that was sent) is given, "projected" holds the same intervals read under that
    rule against the renderer's own marks in this state (spans.py): for each, the value it
    is the hull of and the mark taken off, with the value's sources, or why it has no such
    reading. The two are kept apart and neither replaces the other. A map that is of another
    state than the reply's evidence names is an error ("map_of_another_state").

    Spans that resolve say whose words were marked. They do not say that the packet was
    complete or that the marked words are all a decision rests on: packet.complete and the
    decision are read from the record as before, and nothing here changes either.
    """
    from summarizer import sourcemap

    from . import spans as readings

    out, found = {}, []
    for question, given in told.items():
        apart = ("spans", "edges")
        kept = {
            key: value for key, value in given.items() if key not in apart and value is not None
        }
        if given["status"] == "spans":
            if not found:
                found.append(mapped())
            source, spans, why = found[0], [], None if found[0] else "no_source_map"
            named = given.get("state_sha256")
            if source and named is not None and named != source.sha256:
                source, why = None, "map_of_another_state"
            for start, end in given["spans"] if source else ():
                pieces = source.resolve(start, end)
                placed = {sourcemap.order(piece, source) for piece in pieces}
                if any(piece["kind"] == "unmapped" for piece in pieces):
                    why = "unmapped_source"
                elif any(piece["kind"] == "observation" for piece in pieces):
                    # The observation section copies session text without a source map of its
                    # own: a span in it is unavailable, never a quote of the session.
                    why = "observation_text_unmapped"
                elif "after" in placed:
                    why = "evidence_after_the_call"
                elif "unknown" in placed:
                    why = why or "evidence_order_unknown"
                spans.append({"at": [start, end], "sources": pieces})
            if why:
                kept |= {"status": "error", "why": why}
            else:
                kept |= {"spans": spans, "map": readings.map_identity(source)}
                kept["strict"] = {"rule": readings.EXACT, "intervals": given.get("intervals")}
                read = None
                if state is not None:
                    read = readings.projected(
                        given["spans"], state, source, given.get("targets"), given.get("edges")
                    )
                for one in (read or {}).get("spans", ()):
                    if one["rule"] is not None:  # the value alone: whose words it is
                        one["sources"] = source.resolve(*one["value"])
                if read is not None:
                    kept["projected"] = read
        out[question] = kept
    return out


def entries_shown(summary, emitted):
    """What a packet's text shows, entry by entry, with the trust Summarizer gave each.

    One item for each entry in the text: its type, the event it came from (seq), where in the
    event it sits when it is a piece of content (ref), how much of its own text the line
    holds, and its trust (user, agent, or a lower-trust source). The trust is what the hook
    that delivered the text implies, never what the text says of itself. No item holds text.

    How much is Summarizer's count: "shown" of "of" characters under a "limit", in the
    entry's own text and not in its display, with "noted" for excerpts printed beside the
    quote. The limit is not what was shown: a request of 21 characters under a limit of 400
    shows 21 of 21. An item says its entry is in the text, and "shown" below "of" says the
    entry was cut. Neither says that a given premise reached Kai. That is settled only by
    finding the premise in the request's own text.
    """
    trust = {key_of(entry): entry.get("trust") for entry in summary["entries"]}
    return [item | {"trust": trust.get(key_of(item))} for item in emitted]


def logged_call(directory, entry):
    """The arguments the hook logged for an action, or None when the log cannot supply them.

    The hook stores every event after redaction, with the hash of the call taken before it.
    The record whose call id and hash both equal this entry's holds the arguments the entry was
    built from: redacted, and whole unless the hook clipped a value, which it reports.
    """
    wanted = (entry.get("call_id"), entry.get("call_sha256"))
    if directory is None or not all(wanted):
        return None
    try:
        records, _ = read_events(os.path.join(directory, EVENTS))
    except OSError:
        return None

    def inside(note):
        path = str(note.get("path", ""))
        return path == "tool_input" or path.startswith(("tool_input.", "tool_input["))

    for record in records:
        event, meta = record.get("event"), record.get("meta")
        if not isinstance(event, dict) or not isinstance(meta, dict) or "tool_input" not in event:
            continue
        if (event.get("tool_use_id"), meta.get("call_sha256")) != wanted:
            continue
        clipped = [note for note in meta.get("clipped", ()) if inside(note)]
        redacted = [note for note in meta.get("redacted", ()) if inside(note)]
        return {
            "arguments": event["tool_input"],
            "missing": sum(note["chars"] - note["kept"] for note in clipped),
            "redactions": sum(sum(note["kinds"].values()) for note in redacted),
        }
    return None


def proposed(entry, arguments=None, logged=None):
    """(tool, arguments as JSON text, account): the call as Kai is shown it.

    arguments is the call's own input when the caller holds it (the gate does). It is redacted
    here exactly as Summarizer's hook redacts what it logs, so a request never carries what the
    packet withheld; the call's identity is the hash the hook took before redaction, which the
    record keeps. logged is logged_call() for the entry: the same arguments as the hook stored
    them. With neither, only the text Summarizer shows for the call is left. That is a reading
    of the call, not the call, so nothing is claimed about how much of the call it holds.
    """
    tool, noted = entry.get("tool", ""), {}
    if arguments is not None:
        shown, source = compact(arguments, noted), "arguments"
        missing = sum(item["chars"] - item["kept"] for item in noted.get("clipped", ()))
        redactions = sum(sum(item["kinds"].values()) for item in noted.get("redacted", ()))
    elif logged is not None:
        shown, source = logged["arguments"], "log"
        missing, redactions = logged["missing"], logged["redactions"]
    else:
        shown, source = {"call": call_words(tool, entry["text"])}, "representation"
        missing = redactions = None  # unknown: a display text does not say what it left out
    # As JSON that can be sent: half a surrogate pair in an argument is the escape JSON gives
    # it, so the text reads back as these same arguments.
    text = writable(json.dumps(shown, ensure_ascii=False, sort_keys=True))
    account = {"call_source": source, "call_chars": len(text)}
    return tool, text, account | {"call_chars_not_shown": missing, "call_redactions": redactions}


OUTCOMES = ("returned", "failed", "denied")  # what a later event reports became of a call


def proposal_seen(entry):
    """Whether the hooks delivered this call's proposal, not only a later event that revealed it.

    Summarizer says so itself (proposal_observed false, and no "proposed" step). A summary made
    before it did carries an inferred "proposed" step at the very event that reported the
    outcome; an outcome at the proposal's own sequence number gives that away.
    """
    if entry.get("proposal_observed") is False:
        return False
    steps = entry.get("lifecycle") or ()
    first = next((step for step in steps if step.get("state") == "proposed"), None)
    if first is None:
        return False
    same = [step for step in steps if step.get("seq") == first.get("seq")]
    return not any(step.get("state") in OUTCOMES for step in same)


def review_of(entry, about):
    """Whether a packet says only what could be known when the call was proposed.

    Counting later events is not enough: a call first seen at its result has none, and its
    outcome is already in the packet. So the call itself must still be only proposed, by its
    status and by every step of its lifecycle, and that proposal must have been observed.
    """
    if entry["type"] != "action" or about["later_events"] != 0:
        return "retrospective"
    steps = [step.get("state") for step in entry.get("lifecycle") or ()]
    pending = entry.get("status") == "proposed" and not any(s in OUTCOMES for s in steps)
    return "pre_action" if pending and proposal_seen(entry) else "retrospective"


def _reported(summary, what):
    session = summary.get("session") if isinstance(summary, dict) else None
    reported = session.get(what) if isinstance(session, dict) else None
    return copy.deepcopy(reported) if isinstance(reported, dict) else None


def embedding_of(summary):
    """What a summary says embedded it, copied; None when it does not say.

    Read from the summary a request is built from, never from a running model: a queued
    trigger is answered later, and what is running then may differ. The copy is the summary's
    own report, kept for tracing. It does not pin weights or probes: a revision of "local" or
    a probe version alone identifies neither.
    """
    return _reported(summary, "embedding")


def detector_of(summary):
    """What a summary says marked it, copied; None when it does not say.

    Summarizer names its detectors by hash: the wording rules, the priors and levels, and its
    own source. It is read from the summary a request is built from, like the embedding, and
    copied as it stands: a hash the summary gives as unknown is unknown here, and a summary
    made before Summarizer said any of this has none. Nothing is worked out from what is
    running now.
    """
    return _reported(summary, "detector")


def provenance(summary):
    """The two things a record keeps about the summary it rests on. Never part of a request."""
    return {"summary_embedding": embedding_of(summary), "summary_detector": detector_of(summary)}


def selector():
    """The revision of the packet selector of the Summarizer this process runs
    (summarizer.history.SELECTOR, MR369). A Summarizer older than that name selects as revision 1,
    and is named so: the policy text alone never stands for the whole input."""
    from summarizer import history

    return getattr(history, "SELECTOR", "history-packet/1")


def request_sha256(body, questions):
    """An identifier for one request: SHA-256 of its canonical JSON.

    Canonical means the request object (model, state, registered questions) dumped with sorted
    keys, Python's default separators and non-ASCII characters escaped, so the text is plain
    ASCII. Two requests have the same identifier exactly when they are the same object. It is
    not a hash of the bytes on the wire: the client serializes the same object in its own key
    order.
    """
    canonical = json.dumps(request(body, questions), sort_keys=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def detect(
    client,
    summary,
    entry,
    max_chars=5000,
    rules=None,
    arguments=None,
    directory=None,
    asked=None,
    explain=False,
    sessions=None,
    maps=None,
    observations=False,
    quantities=False,
):
    """Send one trigger's packet to Kai and return the detection record.

    observations (the operator's `serve --session-observations on`) adds to a decision
    request the section of session observations this server computes for the call
    (observe.py, agentarmor-observations/v1). The packet is budgeted exactly as without it,
    so the session's lines and the call are unchanged; the record keeps the typed account
    under "observations" and names the version under layout. quantities (the operator's
    `--observation-quantities on`, with observations on) adds part B, cumulative quantities
    (quantities.py), to that section; without it the section is part A's byte for byte.

    maps, when given, receives source_map() for each request made up, under its
    request_sha256, before anything is sent: for a caller that needs to know where a
    source's characters stand in the state (projecting span targets). It never enters a
    record. Without it a map is made only if an answer comes back with spans to resolve.

    An action is put to Kai for a decision (allow or deny under the rules) and for whether
    untrusted content is redirecting the agent; other triggers only for the second. The call is
    shown from arguments when the caller holds them, else from the session's event log in
    directory, else as Summarizer's display text, which never counts as the whole call.

    max_chars is the budget for the whole state Kai reads: the rules, Summarizer's packet and
    the proposed call. The rules and the call are never cut to fit; the packet gets the room
    they leave, and when that is less than the smallest packet nothing is sent. A state the
    service refuses as too large is rebuilt from a smaller packet, which then says what it
    left out.

    asked, when given, holds the requests already put to Kai for this same summary, by
    request_sha256. A trigger whose request is one of them is not sent again: it takes that
    answer, and its record names the trigger the answer was given for (same_request_as). A
    refusal is remembered too, so a request the service turned away is not sent to be turned
    away again; that is never a link, because a refusal is not an answer. Pass one dict for the
    triggers of one summary and never across summaries: the same words asked at a later time
    are a later decision.

    The record's "requests" counts, for this trigger alone, each thing that can happen to a
    request, apart: built (a request was made up to be asked), refused_before_transport (it
    was refused here for its size on the wire, and went nowhere), transport_attempts (it was
    handed to the transport, or in process to the model's own service), responses (something
    came back, an answer or a refusal) and answers_admitted. An attempt is not a delivery: a
    connection that failed was attempted. requests_sent is transport_attempts, under the name
    records have always had: none when the answer was one already given or when every request
    was refused here, more than one when a larger request was refused by the service first.

    packet.request_sha256 names the last request made up, as it always has: it says what
    would be asked, and "requests" says whether it was. last_request says what became of
    that one request alone (handed to the transport, answered, admitted): a retry is
    another request, and what an earlier one met is not carried over to it. A receipt names
    a request only once it was handed to the transport (decide.Decider.receipt).

    explain adds "shown" to the record: the entries the packet's text shows (entries_shown).
    It is for reading what a request rests on, and is left out of the records a monitor keeps.

    sessions, when given, is what other sessions hold for the call (packet()): the state then
    carries Summarizer's EARLIER SESSIONS section in the packet's room, and the packet is
    complete only if that history is complete and every other session could be read.

    status is "scored", "call_too_large", "policy_too_large", "too_large_to_send" (refused
    here for its bytes on the wire, with nothing sent), "kai_rejected" (refused by the
    service), "kai_unavailable" or "kai_mismatch" (what answered is not the configured model).
    """
    session = summary["session"]
    rules = load_rules() if rules is None else rules
    record = {"schema": SCHEMA, "ts": round(time.time(), 3), "key": key_of(entry)}
    record |= {"agent": session.get("agent"), "session": session.get("session_id")}
    kept = ("type", "seq", "call_id", "call_sha256", "tool", "status", "level", "confidence")
    record["trigger"] = {
        key: entry.get(key) for key in (*kept, "labels") if entry.get(key) is not None
    }
    record |= {"advisory": True, "policy_ready": False, "kai": None}
    record |= {"decision": None, "flagged": None, "rules": [rule["id"] for rule in rules]}
    # What Kai was given is identified by both: the state's layout and the packet's schema.
    record["layout"] = {"state": _policy.LAYOUT, "packet": session.get("schema")}
    record["layout"]["selector"] = selector()  # how the packet was selected (MR369)
    # What embedded and what marked this summary, by its own report. Kept beside the request,
    # never inside it.
    record |= provenance(summary)
    call, account, questions = None, {}, (XPIA,)
    if entry["type"] == "action":
        logged = logged_call(directory, entry) if arguments is None else None
        tool, shown, account = proposed(entry, arguments, logged)
        call, questions = (tool, shown), (DECISION, XPIA)
    observed = None
    if observations and call is not None:
        observed = _observations(summary, entry, shown, account, sessions, quantities)
        # The record keeps references and counts, never the section's copied text (MR299 P1).
        record["observations"] = observed["kept"]
        record["layout"]["observations"] = observed["kept"]["version"]
        if quantities:
            record["layout"]["observations"] += "+" + observed["kept"]["quantities"]["version"]
        observed = observed["lines"]
    # What the rules and the call leave. The observation section is outside this budget, so a
    # packet is the same with the option on as off (observe.py).
    size = packet_room(rules, call, max_chars)
    counts = dict.fromkeys(COUNTED, 0)
    record |= {"requests_sent": 0, "requests": counts}
    # The requests this one decision had refused, when no record of earlier ones is given (a
    # gate's decision): a smaller budget that rebuilds the very same request is never sent again,
    # and the budget is halved on until the request changes or the floor is reached.
    refused_here = {}
    # How the budget came down after refusals, apart from what was sent: each halving, and each
    # rebuilt request that had been refused before and so was not sent again (MR251).
    retries = record["refusal_retries"] = {"budget_reductions": 0, "repeats_not_sent": 0}
    while True:
        text, about, emitted = packet(summary, entry, max(size, MIN_CHARS), sessions)
        if explain:
            record["shown"] = entries_shown(summary, emitted)
        # Whole means the call's own arguments, all of them: a display text never qualifies.
        whole = account.get("call_source") != "representation"
        whole = whole and not account.get("call_chars_not_shown")
        record["packet"] = about | account | {"complete": bool(about["ready"] and whole)}
        if entry["type"] == "action":
            record["packet"]["proposal_observed"] = proposal_seen(entry)
        record["review"] = review_of(entry, about)
        if size < MIN_CHARS:  # no room for the session: refused, not cut
            fits_alone = max_chars - len(state(rules, "")) >= MIN_CHARS
            return record | {"status": "call_too_large" if fits_alone else "policy_too_large"}
        body = state(rules, text, call, observed)
        key = request_sha256(body, questions)
        record["packet"] |= {"state_chars": len(body), "request_sha256": key}
        # The state is measured too, without the observation section, which is outside the
        # budget (MR311 P1): one longer than max_chars is over budget, does not fit, and is
        # sent only as incomplete.
        if len(state(rules, text, call)) > max_chars:
            record["packet"] |= {"fits": False, "ready": False, "complete": False}
            record["packet"]["over_budget"] = True

        room = max(size, MIN_CHARS)

        def mapped(body=body, room=room):
            return source_map(rules, summary, entry, room, sessions, call, body, observed)

        if maps is not None:
            maps[key] = mapped()
        # What became of this request, and of no other: an earlier, larger request that was
        # answered with a refusal says nothing about whether this one reached anything.
        last = {"request_sha256": key, "transport_attempts": 0, "responded": False}
        record["last_request"] = last | {"admitted": False}
        earlier = asked.get(key) if asked is not None else refused_here.get(key)
        try:
            if earlier is None:
                chosen = chosen_for(client, record["packet"])  # this server's, from the packet
                reply = put(client, body, questions, counts, record["last_request"], chosen)
            elif earlier["reply"] is None:  # refused before, and how: nothing is sent again
                retries["repeats_not_sent"] += 1
                raise (KaiUnsent if earlier.get("unsent") else KaiRejected)("refused before")
            else:
                reply = earlier["reply"]
        except KaiRejected as error:
            here = isinstance(error, KaiUnsent)  # refused here, with nothing sent
            if earlier is None:
                record["requests_sent"] = counts["transport_attempts"]
                refusal = {"key": record["key"], "reply": None, "unsent": here}
                (asked if asked is not None else refused_here)[key] = refusal
            status = "too_large_to_send" if here else "kai_rejected"
            # What must be sent whole (the rules and the call) may alone be too many bytes:
            # then no smaller packet will pass either, and none is tried.
            alone = here and wire_bytes(request(state(rules, "", call, observed), questions))
            alone = alone > MAX_BYTES
            if size <= MIN_CHARS or alone:
                return record | {"status": status}
            size = max(MIN_CHARS, size // 2)
            retries["budget_reductions"] += 1
            continue
        except KaiUnavailable as error:
            record["requests_sent"] = counts["transport_attempts"]
            return record | {"status": "kai_unavailable", "error": str(error)}
        except KaiMismatch as error:
            # Something answered, and it is not the model this monitor was set up for. Its
            # answer is not used and not kept; what differed is (model, readout or package).
            record["requests_sent"] = counts["transport_attempts"]
            return record | {"status": "kai_mismatch", "error": str(error)}
        record["requests_sent"] = counts["transport_attempts"]
        # Only the request that produced the answer in hand says whose answer it is. Earlier
        # attempts in this loop, refused or reused as refusals, leave no mark on that.
        if earlier is not None:
            record["same_request_as"] = earlier["key"]
        elif asked is not None:
            asked[key] = {"key": record["key"], "reply": reply}
        answers = reply["answers"]
        record["kai"] = {"p_xpia": answers[XPIA], **reply["service"]}
        if DECISION in answers:
            record["kai"]["p_deny"] = answers[DECISION]["p_deny"]
            record["decision"] = answers[DECISION]["choice"]  # Kai's answer: allow or deny
        if reply.get("evidence"):  # spans, or why there are none; never part of the verdict
            record["spans"] = spans_record(reply["evidence"], mapped, body)
        record["flagged"] = record["decision"] == "deny" or answers[XPIA] >= FLAG_AT
        # Summarizer marked it and Kai cleared it: kept apart from "nothing was found".
        marked = LEVELS.get(entry.get("level"), 0) >= LEVELS["medium"]
        record["disagreement"] = bool(marked and not record["flagged"])
        return record | {"status": "scored"}


def _observations(summary, entry, shown, account, sessions, quantities=False):
    """{"account", "lines", "kept"}: observe.observe() for the call as the request shows it, over
    this session up to the call and the other sessions as they stood at the call's time; "kept"
    is observe.persisted(), the only part a record holds."""
    from summarizer import history

    from . import observe

    try:
        arguments = json.loads(shown)
    except ValueError:
        arguments = None
    if sessions is not None:
        mine = sessions["mine"]
        others = history.cut_at(sessions["others"], float(entry.get("ts") or 0))
    else:
        mine = {"summary": summary, "cwd": str(summary["session"].get("cwd") or ""), "alias": None}
        others = []
    found = observe.observe(
        mine, entry, arguments, account.get("call_source"), others, quantities=quantities
    )
    if sessions is None:
        found["coverage"]["other_sessions"] = "not read (earlier sessions off)"
    lines = observe.render(found)
    return {"account": found, "lines": lines, "kept": observe.persisted(found, lines)}


# What can happen to a request, each counted apart (see detect).
COUNTED = ("built", "refused_before_transport", "transport_attempts", "responses")
COUNTED += ("answers_admitted",)


def put(client, body, questions, counts, last, session_route=False):
    """client.ask, with what became of the request counted where it is known: at the
    client, which alone can tell a request it refused from one it sent. counts adds up
    every request of a trigger; last is this one request's own.

    A client of two checkpoints (route.Routed) is told this server's own choice for the
    packet (session_route) and may hand the transport more than one request for the reply."""
    counts["built"] += 1
    try:
        if getattr(client, "routed", False):
            reply = client.ask(body, questions, session_route=session_route)
        else:
            reply = client.ask(body, questions)
    except (KaiRejected, KaiUnavailable, KaiMismatch) as error:
        counts["refused_before_transport"] += isinstance(error, KaiUnsent)
        counts["transport_attempts"] += error.attempts
        counts["responses"] += bool(error.responded)
        last |= {"transport_attempts": error.attempts, "responded": bool(error.responded)}
        raise
    made = reply.get("attempts", 1)
    counts["transport_attempts"] += made
    counts["responses"] += made
    counts["answers_admitted"] += 1
    last |= {"transport_attempts": made, "responded": True, "admitted": True}
    return reply


def append(directory, record):
    """Add a detection to a session's file, readable by its owner only."""
    path = os.path.join(directory, DETECTIONS)
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(handle, "a", encoding="utf-8") as out:
        out.write(writable(json.dumps(record, ensure_ascii=False, allow_nan=False)) + "\n")


def read(directory):
    """A session's detections, oldest first; the latest record for a trigger is its state."""
    path = os.path.join(directory, DETECTIONS)
    try:
        with open(path, encoding="utf-8") as lines:
            return [json.loads(line) for line in lines if line.strip()]
    except FileNotFoundError:
        return []


# Statuses that need no further attempt.
FINAL = ("scored", "kai_rejected", "call_too_large", "policy_too_large", "too_large_to_send")


def settled(records):
    """Keys of triggers that need no further attempt: answered, or refused for their size."""
    return {r["key"] for r in records if r.get("status") in FINAL}


def latest(records):
    """The state of each trigger: its last record, except that an answer is never replaced by
    a later note that Kai could not be reached (a gate and the daemon may both ask about a call).
    """
    state = {}
    for record in records:
        held = state.get(record["key"])
        if held is None or record.get("status") in FINAL or held.get("status") not in FINAL:
            state[record["key"]] = record
    return state
