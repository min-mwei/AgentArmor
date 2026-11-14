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
from .policy import LAYOUT, load_rules, state, writable

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
    and every other session listed could be read up to date: a section that left something
    out, or sessions nobody could read, are no evidence that nothing more bears on the call.
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
    ready = bool(shown["fits"] and not gaps and coverage["scanned_all_captured"])
    lookup = None
    if sessions is not None:
        account = shown.get("history") or {}
        counts = sessions["counts"]
        known = sum(counts[k] for k in ("no_summary_or_unreadable", "summary_behind_its_log"))
        lookup = {"complete": account.get("complete", True), "why": account.get("why", [])}
        lookup |= {"coverage_known": known + counts["beyond_the_cap"] == 0, "sessions": counts}
        lookup |= {
            "sources": len(account.get("sources", ())),
            "lines": len(account.get("items", ())),
        }
        lookup |= {"route": shown.get("route"), "route_reason": shown.get("route_reason")}
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
        "fits": shown["fits"],
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
):
    """Send one trigger's packet to Kai and return the detection record.

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
    record["layout"] = {"state": LAYOUT, "packet": session.get("schema")}
    # What embedded and what marked this summary, by its own report. Kept beside the request,
    # never inside it.
    record |= provenance(summary)
    call, account, questions = None, {}, (XPIA,)
    if entry["type"] == "action":
        logged = logged_call(directory, entry) if arguments is None else None
        tool, shown, account = proposed(entry, arguments, logged)
        call, questions = (tool, shown), (DECISION, XPIA)
    size = max_chars - len(state(rules, "", call))  # what the rules and the call leave
    counts = dict.fromkeys(COUNTED, 0)
    record |= {"requests_sent": 0, "requests": counts}
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
        body = state(rules, text, call)
        key = request_sha256(body, questions)
        record["packet"] |= {"state_chars": len(body), "request_sha256": key}
        # What became of this request, and of no other: an earlier, larger request that was
        # answered with a refusal says nothing about whether this one reached anything.
        last = {"request_sha256": key, "transport_attempts": 0, "responded": False}
        record["last_request"] = last | {"admitted": False}
        earlier = asked.get(key) if asked is not None else None
        try:
            if earlier is None:
                reply = put(client, body, questions, counts, record["last_request"])
            elif earlier["reply"] is None:  # refused before, and how: nothing is sent again
                raise (KaiUnsent if earlier.get("unsent") else KaiRejected)("refused before")
            else:
                reply = earlier["reply"]
        except KaiRejected as error:
            here = isinstance(error, KaiUnsent)  # refused here, with nothing sent
            if earlier is None:
                record["requests_sent"] = counts["transport_attempts"]
                if asked is not None:
                    asked[key] = {"key": record["key"], "reply": None, "unsent": here}
            status = "too_large_to_send" if here else "kai_rejected"
            # What must be sent whole (the rules and the call) may alone be too many bytes:
            # then no smaller packet will pass either, and none is tried.
            alone = here and wire_bytes(request(state(rules, "", call), questions)) > MAX_BYTES
            if size <= MIN_CHARS or alone:
                return record | {"status": status}
            size = max(MIN_CHARS, size // 2)
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
        record["flagged"] = record["decision"] == "deny" or answers[XPIA] >= FLAG_AT
        # Summarizer marked it and Kai cleared it: kept apart from "nothing was found".
        marked = LEVELS.get(entry.get("level"), 0) >= LEVELS["medium"]
        record["disagreement"] = bool(marked and not record["flagged"])
        return record | {"status": "scored"}


# What can happen to a request, each counted apart (see detect).
COUNTED = ("built", "refused_before_transport", "transport_attempts", "responses")
COUNTED += ("answers_admitted",)


def put(client, body, questions, counts, last):
    """client.ask, with what became of the request counted where it is known: at the
    client, which alone can tell a request it refused from one it sent. counts adds up
    every request of a trigger; last is this one request's own."""
    counts["built"] += 1
    try:
        reply = client.ask(body, questions)
    except (KaiRejected, KaiUnavailable, KaiMismatch) as error:
        counts["refused_before_transport"] += isinstance(error, KaiUnsent)
        counts["transport_attempts"] += error.attempts
        counts["responses"] += bool(error.responded)
        last |= {"transport_attempts": error.attempts, "responded": bool(error.responded)}
        raise
    counts["transport_attempts"] += 1
    counts["responses"] += 1
    counts["answers_admitted"] += 1
    last |= {"transport_attempts": 1, "responded": True, "admitted": True}
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
