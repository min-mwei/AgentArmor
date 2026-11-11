"""The per-call decision, as AgentArmor's local server makes it.

A gate names a call (local.py). Everything the decision rests on is the server's own: the
rules it was started with, its record of the session, the call as the hook logged it, and the
decision model it was configured for. Nothing the caller says is evidence, policy or
provenance. What goes back is a receipt bound to that call, to how far the session's record
went when it was read, to the exact request the decision model was given and to the rules.

A check that cannot be completed is not a pass: it is "undecided" with the reason, and the
gate applies its own setting for that. An "allow" stands only on complete evidence from
before the call, from the configured decision model, and only if the user has not spoken
since the evidence was read.

Nothing here is a qualified policy (policy_ready false in every record and receipt).
"""

import hashlib
import json
import threading

from summarizer.store import AGENTS, EVENTS, load_summary, read_events, session_dir

from .escalate import LEVELS, detect
from .gate import keep, note, outcome
from .local import ON_ERROR, RECEIPT
from .policy import LAYOUT, load_rules

SLOTS = 4  # calls decided at once; a caller beyond that is told so, and never queued unseen


def policy_identity(rules):
    """What names the rules a decision was made under: their ids, and a hash of all of them."""
    canonical = json.dumps(rules, sort_keys=True)
    digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    return {"rules": [rule["id"] for rule in rules], "sha256": digest}


def user_spoke_since(directory, log_bytes):
    """Whether the session's log holds something from the user after the first log_bytes.

    A request typed while a call waits for its check can change what the user wants: limit
    it, withdraw it, ask for something else. So can an answer the user gives to a question
    the agent asked. An event that cannot be read counts: nothing says it was not one.
    """
    if not isinstance(log_bytes, int):
        return True
    try:
        later, _ = read_events(directory / EVENTS, offset=log_bytes)
    except OSError:
        return True
    for record in later:
        event = record.get("event")
        if not isinstance(event, dict):
            return True
        name = event.get("hook_event_name")
        answered = name == "PostToolUse" and event.get("tool_name") == "AskUserQuestion"
        if name == "UserPromptSubmit" or answered:
            return True
    return False


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
):
    """(verdict, record, why) for one proposed call, named as a hook event names it.

    identity is the hash of the call its asker holds. The call itself is taken from the
    session's event log, where the hook put it, and counts only with that hash. client is
    the way to the decision model, or None when there is none.

    verdict is "abstain", "deny" or "undecided". record is the detection when the decision
    model was asked; the outcome alone when it was read off a summary without asking; else
    None. why names, for "undecided", what kept the check from being completed. A record
    says how far the session's log went when its evidence was read ("evidence").
    """
    if not isinstance(session, str) or not session or not isinstance(call_id, str) or not call_id:
        return "undecided", None, "event"
    directory = session_dir(agent, session)
    summary = load_summary(agent, directory.name, wait, call_id, identity)
    if summary is None or not summary.get("fresh") or summary.get("call") != "verified":
        return "undecided", None, "no_summary"  # nothing about this exact call to decide on
    entry = next(
        e for e in summary["entries"] if e.get("call_id") == call_id and e["type"] == "action"
    )
    coverage = summary["session"]["coverage"]
    read_to = {"events": coverage.get("events"), "log_bytes": coverage.get("log_bytes")}

    def unasked(why):  # an outcome read off the summary, with nobody asked
        found = outcome(agent, session, call_id, why, summary) | {"evidence": read_to}
        return "undecided", found, why

    if LEVELS.get(entry.get("level"), 0) < LEVELS[level]:
        # "Not marked" is a finding only when Summarizer scanned everything it captured.
        if coverage["scanned_all_captured"]:
            return "abstain", None, None
        return unasked("coverage")
    if client is None:  # marked, and nothing to put it to
        return unasked("no_decision_model")
    record = detect(client, summary, entry, max_chars, rules, directory=directory, explain=explain)
    record["evidence"] = read_to
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
    # And only if the user has said nothing since that evidence was read: the answer took
    # time, and a request made meanwhile is not in what the decision model saw.
    if user_spoke_since(directory, read_to["log_bytes"]):
        return "undecided", record, "moved_on"
    return "abstain", record, None


def malformed(asked):
    """Why a question is not one a gate asks, or None when it is."""
    names = ("agent", "session_id", "tool_use_id", "call_sha256", "on_error")
    if not isinstance(asked, dict) or set(asked) != set(names):
        return "a question names an agent, a session, a call, the call's hash and on_error"
    if any(not isinstance(asked[name], str) or not asked[name] for name in names):
        return "every part of a question is a non-empty string"
    if asked["agent"] not in AGENTS or asked["on_error"] not in ON_ERROR:
        return "an unknown agent or on_error"
    if any(len(asked[name]) > 512 for name in names):
        return "a part of the question is too long to be an id"
    return None


class Decider:
    """The local server's answer to a gate: one proposed call, decided on the server's terms.

    client is the way to the decision model the server was configured for, or None. rules,
    level, max_chars and wait are the server's: a gate cannot bring its own.
    """

    def __init__(self, client, rules=None, level="medium", max_chars=5000, wait=3.0, slots=SLOTS):
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
            "level": self.level,
            "max_chars": self.max_chars,
            "layout": LAYOUT,
            "at_once": self.slots,
        }

    def decide(self, asked):
        """The receipt for one question. Never raises: a check that failed is not a pass."""
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
                )
            except Exception as error:
                note(asked["agent"], error, "failed")
                verdict, record, why = "undecided", None, "failed"
            finally:
                self.room.release()
        denied = verdict == "deny" or (verdict == "undecided" and asked["on_error"] != "abstain")
        if record is not None or verdict != "abstain":
            payload = {"session_id": asked["session_id"], "tool_use_id": asked["tool_use_id"]}
            keep(asked["agent"], payload, record, verdict, why, "deny" if denied else "none")
        return self.receipt(asked, verdict, why, record)

    def receipt(self, asked, verdict, why, record=None):
        """What a gate is told: the verdict, and what it is bound to. No session text."""
        named = ("agent", "session_id", "tool_use_id", "call_sha256")
        bound = {name: asked.get(name) for name in named}
        bound = {name: value if isinstance(value, str) else None for name, value in bound.items()}
        bound |= {"policy": self.policy, "layout": LAYOUT}
        told = {"schema": RECEIPT, "verdict": verdict, "why": why, "p_deny": None}
        if record is not None:
            packet, kai = record.get("packet") or {}, record.get("kai") or {}
            told["p_deny"] = kai.get("p_deny")
            bound |= {
                # How far the session's record went, the exact request, and who answered it.
                "evidence": record.get("evidence"),
                "request_sha256": packet.get("request_sha256"),
                "complete": packet.get("complete"),
                "review": record.get("review"),
                "decision_model": {
                    name: kai.get(name) for name in ("model", "readout", "package_sha256")
                }
                if kai
                else None,
                "summary_embedding": record.get("summary_embedding"),
                "summary_detector": record.get("summary_detector"),
            }
        return told | {"bound": bound, "advisory": True, "policy_ready": False}
