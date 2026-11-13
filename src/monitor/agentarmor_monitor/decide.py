"""The per-call decision, as AgentArmor's local server makes it.

A gate names a call (local.py). Everything the decision rests on is the server's own: the
rules it was started with, its record of the session, the call as the hook logged it, and the
decision model it was configured for. Nothing the caller says is evidence, policy or
provenance. What goes back is a receipt bound to that call and that asking, to how far the
session's record went when it was read, to the exact request the decision model was given
and to the rules.

A check that cannot be completed is not a pass: it is "undecided" with the reason, and the
gate applies its own setting for that. An "allow" stands only on complete evidence from
before the call, from the configured decision model, and only if it still applies when it
arrives: every byte the session logged while the answer was awaited has been read, the user
said nothing in it, and nothing in it reports what became of the call.

The server records the decision it issued and what the gate said it would do without one.
It never records what the gate did: it cannot know. A gate that stopped waiting has stopped
the call whatever is answered later, and writes that itself (gate.py).

Nothing here is a qualified policy (policy_ready false in every record and receipt).
"""

import hashlib
import json
import os
import threading
import time

from summarizer.store import AGENTS, EVENTS, load_summary, read_events, session_dir

from .escalate import LEVELS, detect
from .gate import keep, note, outcome
from .local import ASKED, MODES, ON_ERROR, RECEIPT
from .policy import LAYOUT, load_rules

SLOTS = 4  # calls decided at once; a caller beyond that is told so, and never queued unseen
SETTLE = 0.5  # seconds an event that is being appended is given to arrive whole
REPORTED = ("PostToolUse", "PostToolUseFailure", "PermissionDenied")  # what became of a call


def policy_identity(rules):
    """What names the rules a decision was made under: their ids, and a hash of all of them."""
    canonical = json.dumps(rules, sort_keys=True)
    digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    return {"rules": [rule["id"] for rule in rules], "sha256": digest}


def later_events(directory, log_bytes, settle=SETTLE):
    """(events logged after the first log_bytes, how far the log was read, whether to its end).

    Every byte after the mark is accounted for, or the answer is "not to its end". The log's
    reader takes whole lines only, so an event that is half written is not among what it
    returns: what tells it from nothing at all is that the log is longer than what was read.
    An append in progress is given a moment to finish. One that never finishes (a full disk,
    a hook that was killed) leaves bytes nobody can read, and that is said, not passed over.
    """
    if not isinstance(log_bytes, int) or isinstance(log_bytes, bool) or log_bytes < 0:
        return [], None, False
    path, deadline = directory / EVENTS, time.monotonic() + settle
    while True:
        try:
            later, read_to = read_events(path, offset=log_bytes)
            size = os.path.getsize(path)
        except OSError:
            return [], None, False
        if read_to == size:
            return later, read_to, True
        if size < log_bytes or time.monotonic() >= deadline:  # a log that shrank, or a tail
            return later, read_to, False  # that stays unreadable
        time.sleep(0.02)


def still_applies(directory, call_id, log_bytes, settle=SETTLE):
    """(why an answer no longer applies or None, what was checked to say so).

    The answer was given on the session as it stood at log_bytes. It applies to the call now
    only if nothing that matters was logged since:

    - "log_unread": the log could not be read to its end, so what was logged is not known;
    - "moved_on": the user made a request or answered a question, or a line cannot be read
      and may have been either. What the user said then is not in what the model read;
    - "outcome_reported": the hooks already report the call as run, failed or refused. A
      verdict cannot guard a call that is no longer waiting for one.

    Something else the agent did meanwhile, such as another call proposed side by side,
    changes nothing.
    """
    later, read_to, whole = later_events(directory, log_bytes, settle)
    checked = {"from_log_bytes": log_bytes, "to_log_bytes": read_to, "to_the_end": whole}
    checked["later_events"] = len(later)
    if not whole:
        return "log_unread", checked
    return overtaken(later, call_id), checked


def overtaken(later, call_id):
    """Why a call's proposal no longer stands given what was logged after it, or None."""
    for record in later:
        event = record.get("event")
        if not isinstance(event, dict):
            return "moved_on"
        name = event.get("hook_event_name")
        answered = name == "PostToolUse" and event.get("tool_name") == "AskUserQuestion"
        if name in REPORTED and event.get("tool_use_id") == call_id:
            return "outcome_reported"
        if name == "UserPromptSubmit" or answered:
            return "moved_on"
    return None


def since_proposal(directory, entry, call_id, log_bytes, settle=SETTLE):
    """(why the proposal no longer stands or None, what was read to say so).

    A summary that is fresh covers the log to its end, and a call whose hash is verified is
    the call that was asked about. Neither says the call is still waiting under the request
    it was proposed under: the summary may already hold the user taking it back, or the
    call's own result. So what the log holds after the proposal is read for exactly that,
    whether or not the call is marked and whether or not a model will be asked.

    It is the same reading as still_applies makes, under the same rule: the log is read to
    its end or the answer is "log_unread". An event half written when this looks is not
    among the lines that can be read, and may be the user taking the call back; it is given
    the same moment to arrive whole, and is never taken for nothing. What was read is said:
    log_bytes is how far the summary had read, and the log must reach at least that far.
    """
    records, read_to, whole = later_events(directory, 0, settle)
    checked = {"summary_log_bytes": log_bytes, "to_log_bytes": read_to, "to_the_end": whole}
    checked["events_after_proposal"] = None
    marked_at = isinstance(log_bytes, int) and not isinstance(log_bytes, bool)
    if not whole or not marked_at or read_to < log_bytes:  # unread, or shorter than was read
        return "log_unread", checked
    seq = entry.get("seq")
    if not isinstance(seq, int) or seq < 1 or seq > len(records):
        return "log_unread", checked
    later = records[seq:]
    checked["events_after_proposal"] = len(later)
    if entry.get("status") != "proposed":
        return "outcome_reported", checked
    return overtaken(later, call_id), checked


def judge(
    agent,
    session,
    call_id,
    identity,
    client,
    level="medium",
    max_chars=5000,
    wait=3.0,
    rules=None,
    explain=False,
    settle=None,
    made_with=None,
    earlier=None,
):
    """(verdict, record, why) for one proposed call, named as a hook event names it.

    identity is the hash of the call its asker holds. The call itself is taken from the
    session's event log, where the hook put it, and counts only with that hash. client is
    the way to the decision model, or None when there is none.

    verdict is "abstain", "deny" or "undecided". record is the detection when the decision
    model was asked, and otherwise what was read off the summary without asking: None only
    when no summary of this call was read at all. why names, for "undecided", what kept the
    check from being completed.

    made_with is what the caller's own Summarizer says makes a summary (a runtime's
    identity()), when the caller has one. A summary that another encoder, other probes or
    other rules made is fresh and is not this server's: its marks are another model's, and
    a call is routed on marks. With made_with given, such a summary is no basis for a
    decision and the verdict is "undecided", why "summary_made_otherwise".

    earlier, when given, is the shadow measurement of what other sessions hold
    (earlier.Shadow). The call is handed to it without waiting, and it keeps its own
    record: no verdict, request, reason or field of the record here reads it, and it
    holds no decision slot.

    A record says how far the session's log had been read when its evidence was taken
    ("evidence"), how the call was routed ("routing": asked or not, and the level and
    threshold that decided it), and for an answer that had to still apply when it came, what
    was checked to say so ("revalidated").
    """
    if not isinstance(session, str) or not session or not isinstance(call_id, str) or not call_id:
        return "undecided", None, "event"
    directory = session_dir(agent, session)
    settle = SETTLE if settle is None else settle
    summary = load_summary(agent, directory.name, wait, call_id, identity, made_with=made_with)
    if summary is None or not summary.get("fresh") or summary.get("call") != "verified":
        if earlier is not None:
            earlier.unmeasured("no_summary")
        return "undecided", None, "no_summary"  # nothing about this exact call to decide on
    if made_with is not None and summary.get("compatible") is not True:
        if earlier is not None:
            earlier.unmeasured("summary_made_otherwise")
        return "undecided", None, "summary_made_otherwise"  # marked by another model or rules
    if earlier is not None:
        earlier.offer(agent, directory.name, call_id, summary, identity)
    entry = next(
        e for e in summary["entries"] if e.get("call_id") == call_id and e["type"] == "action"
    )
    coverage = summary["session"]["coverage"]
    read_to = {"events": coverage.get("events"), "log_bytes": coverage.get("log_bytes")}
    read_to["scanned_all_captured"] = bool(coverage["scanned_all_captured"])
    # The workspace, by a hash of the directory the session runs in: a receipt holds no path.
    where = str(summary["session"].get("cwd") or "")
    read_to["workspace_sha256"] = hashlib.sha256(where.encode("utf-8", "replace")).hexdigest()
    marked = LEVELS.get(entry.get("level"), 0) >= LEVELS[level]
    # eligible: marked at the threshold. requests_sent: how many requests were handed to
    # the transport, and requests: everything that became of them, apart (escalate.detect).
    routing = {"eligible": marked, "requests_sent": 0, "requests": None}
    routing |= {"level": entry.get("level"), "threshold": level}
    # Does the proposal still stand? Asked before any routing, of every call, by reading the
    # log to its end. The moment an unfinished append is given is one, shared with the
    # check made after an answer: what this reading waits is not waited again.
    began = time.monotonic()
    stale, checked = since_proposal(directory, entry, call_id, read_to["log_bytes"], settle)
    settle = max(0.0, settle - (time.monotonic() - began))
    applicable = {"before_routing": stale is None, "why": stale} | checked

    def unasked(verdict, why, reason):  # read off the summary, with nobody asked
        found = outcome(agent, session, call_id, why or "unmarked", summary)
        found |= {"evidence": read_to, "routing": routing | {"reason": reason}}
        return verdict, found | {"applicable": applicable}, why

    if stale:  # overtaken, or not known: the log could not be read to its end
        unknown = stale == "log_unread"
        return unasked("undecided", stale, "proposal_unknown" if unknown else "proposal_overtaken")
    if not marked:
        # "Not marked" is a finding only when Summarizer scanned everything it captured.
        if coverage["scanned_all_captured"]:
            return unasked("abstain", None, "below_threshold")
        return unasked("undecided", "coverage", "below_threshold_with_gaps")
    if client is None:  # marked, and nothing to put it to
        return unasked("undecided", "no_decision_model", "no_decision_model")
    record = detect(client, summary, entry, max_chars, rules, directory=directory, explain=explain)
    counts = record.get("requests") or {}
    sent = counts.get("transport_attempts", 0)
    reason = "marked" if sent else "marked_and_not_sent"  # too large to put to the model
    record |= {"evidence": read_to, "applicable": applicable}
    record["routing"] = routing | {"requests_sent": sent, "requests": counts, "reason": reason}
    if record["status"] != "scored":
        return "undecided", record, record["status"]
    if record["decision"] == "deny":
        return "deny", record, None
    if record["decision"] != "allow":
        return "undecided", record, "failed"
    # An allow counts only on complete evidence from before the call.
    if not record["packet"]["complete"]:
        return "undecided", record, "incomplete"
    if record["review"] != "pre_action":
        return "undecided", record, "moved_on"
    # And only if it still applies now that it has come: the answer took time.
    stale, record["revalidated"] = still_applies(directory, call_id, read_to["log_bytes"], settle)
    if stale:
        return "undecided", record, stale
    return "abstain", record, None


def malformed(asked):
    """Why a question is not one a gate asks, or None when it is."""
    if not isinstance(asked, dict) or set(asked) != set(ASKED):
        return "a question names an agent, a session, a call, its hash, an attempt and on_error"
    if any(not isinstance(asked[name], str) or not asked[name] for name in ASKED):
        return "every part of a question is a non-empty string"
    if asked["agent"] not in AGENTS or asked["on_error"] not in ON_ERROR:
        return "an unknown agent or on_error"
    if any(len(asked[name]) > 512 for name in ASKED):
        return "a part of the question is too long to be an id"
    return None


class Decider:
    """The local server's answer to a gate: one proposed call, decided on the server's terms.

    client is the way to the decision model the server was configured for, or None. rules,
    level, max_chars and wait are the server's: a gate cannot bring its own. mode says what
    its decisions are for: "enforce", or "shadow" when they are issued to be recorded and
    measured and never applied. It is in every receipt, and a gate does not apply a shadow one.
    """

    def __init__(
        self,
        client,
        rules=None,
        level="medium",
        max_chars=5000,
        wait=3.0,
        slots=SLOTS,
        mode="enforce",
        made_with=None,
        earlier=None,
    ):
        if mode not in MODES:
            raise ValueError(f"a server's mode is one of {MODES}")
        self.mode = mode
        # What this server's own Summarizer says makes a summary. A summary made otherwise
        # is not decided on. None when the server does not know (then nothing is checked).
        self.made_with = made_with
        # The shadow measurement of what other sessions hold (earlier.Shadow), or None.
        self.earlier = earlier
        self.client = client
        self.rules = load_rules() if rules is None else rules
        self.level, self.max_chars, self.wait = level, max_chars, wait
        self.policy = policy_identity(self.rules)
        self.room = threading.BoundedSemaphore(slots)
        self.slots = slots

    def about(self):
        """What a decision here rests on, for /health: no session text, and no secret."""
        return {
            "policy": self.policy,
            "mode": self.mode,
            "level": self.level,
            "max_chars": self.max_chars,
            "layout": LAYOUT,
            "at_once": self.slots,
            "summary_identity_checked": self.made_with is not None,
            "earlier_sessions": self.earlier.about() if self.earlier else "off",
        }

    def decide(self, asked):
        """The receipt for one question. Never raises: a check that failed is not a pass.

        What is put on record is the decision as issued: the verdict, the asking it answers,
        the rules and the evidence, and what the gate said it would do without a verdict.
        Whether the gate got the receipt, and what it did, is the gate's to record.
        """
        if malformed(asked):  # nothing is kept of what is not a question
            return self.receipt(asked if isinstance(asked, dict) else {}, "undecided", "request")
        if not self.room.acquire(blocking=False):
            # Told at once, and put on record like any check that was not completed.
            verdict, record, why = "undecided", None, "busy"
        else:
            try:
                verdict, record, why = judge(
                    asked["agent"],
                    asked["session_id"],
                    asked["tool_use_id"],
                    asked["call_sha256"],
                    self.client,
                    self.level,
                    self.max_chars,
                    self.wait,
                    self.rules,
                    made_with=self.made_with,
                    earlier=self.earlier,
                )
            except Exception as error:
                note(asked["agent"], error, "failed")
                verdict, record, why = "undecided", None, "failed"
            finally:
                self.room.release()
        if record is None:  # no summary of the call was read: the outcome alone
            record = outcome(asked["agent"], asked["session_id"], asked["tool_use_id"], why)
        record |= {"gate": True, "stage": "issued", "attempt": asked["attempt"], "mode": self.mode}
        record |= {"verdict": verdict, "declared_on_error": asked["on_error"]}
        record |= {"call_id": asked["tool_use_id"], "call_sha256": asked["call_sha256"]}
        record |= {"policy_sha256": self.policy["sha256"]}
        if why:
            record["why"] = why
        keep(asked["agent"], asked["session_id"], record)
        return self.receipt(asked, verdict, why, record)

    def receipt(self, asked, verdict, why, record=None):
        """What a gate is told: the verdict, and what it is bound to. No session text.

        Where the decision model was not asked, what it would have been asked and what it
        answered are given as None: nothing is made up to look like a request.
        """
        named = ("agent", "session_id", "tool_use_id", "call_sha256", "attempt")
        bound = {name: asked.get(name) for name in named}
        bound = {name: value if isinstance(value, str) else None for name, value in bound.items()}
        bound |= {"policy": self.policy, "layout": LAYOUT}
        told = {"schema": RECEIPT, "verdict": verdict, "why": why, "p_deny": None}
        told["mode"] = self.mode
        record = record or {}
        packet, kai = record.get("packet") or {}, record.get("kai") or {}
        last = record.get("last_request") or {}
        if last.get("request_sha256") != packet.get("request_sha256"):
            last = {}  # not about the request this receipt names
        told["p_deny"] = kai.get("p_deny")
        answered = {name: kai.get(name) for name in ("model", "readout", "package_sha256")}
        bound |= {
            # How far the session's log had been read, and in which workspace; how the call
            # was routed; and for an answer that had to still apply, what was checked.
            "evidence": record.get("evidence"),
            "routing": record.get("routing"),
            "applicable": record.get("applicable"),
            "revalidated": record.get("revalidated"),
            # The exact request, once it was handed to the transport, and what answered
            # it. "complete" is about a state that reached a model's service: it is null
            # unless something came back for a request, an answer or a refusal. A request
            # refused here for its size, or sent into a connection that failed, is not
            # known to have been given to anything.
            # Both are said of the last request alone, by what became of that request:
            # an earlier, different request that was answered proves nothing about it.
            "request_sha256": packet.get("request_sha256")
            if last.get("transport_attempts")
            else None,
            "complete": packet.get("complete") if last.get("responded") else None,
            "last_request": record.get("last_request"),
            "review": record.get("review"),
            "decision_model": answered if kai else None,
            "summary_embedding": record.get("summary_embedding"),
            "summary_detector": record.get("summary_detector"),
        }
        return told | {"bound": bound, "advisory": True, "policy_ready": False}
