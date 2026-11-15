"""A second decision checkpoint for calls an earlier session bears on. Research only, shadow.

Off unless a server is started with a session model (cli: --kai-session-url). Then two
checkpoints answer, each named by its own package, and which one answers a question is this
server's choice from its own record of the packet, never a caller's and never from text a
model reads:

    is_xpia_attempt   always the default checkpoint
    decision          the session checkpoint when session_adapter(packet) holds; else the default

Both answers are required. One that is missing, refused, late or from another package leaves
the whole request without an answer, exactly as it does with one checkpoint: nothing is
dropped, shortened or taken from the other checkpoint to make a reply look complete.

Choosing a checkpoint grants nothing. Whether an allow counts is still decided where it was
(decide.py): complete evidence from before the call, and the call still the one decided.

Standard library only.
"""

import hashlib
import json

from .kai import DECISION, XPIA, KaiMismatch, KaiRejected, KaiUnavailable, marked, request

POLICY = "session-route/1"
REASON = "earlier_session_bears_on_call"


def session_adapter(packet):
    """Whether the session checkpoint answers this packet's decision. Every condition is read
    from the server's own account of the packet as it stood before the call, and each must be
    exactly true: the lookup found that an earlier session bears on the call (history.select),
    its history is complete, every session's coverage is known, and the packet itself is ready
    and complete (it fits, nothing key was dropped or clipped, no condition is unresolved,
    everything captured was scanned, and the proposed call is the logged one, whole). A field
    that is absent is a condition that does not hold."""
    if not isinstance(packet, dict):
        return False
    lookup = packet.get("history")
    if not isinstance(lookup, dict):
        return False
    return (
        packet.get("ready") is True
        and packet.get("complete") is True
        and lookup.get("route") == "asked"
        and lookup.get("route_reason") == REASON
        and lookup.get("complete") is True
        and lookup.get("coverage_known") is True
    )


def _named(state, question):
    canonical = json.dumps(request(state, (question,)), sort_keys=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class Routed:
    """Two clients as one: the default checkpoint and the session checkpoint.

    Each client must have been set up for a package (kai.profile), and for different ones:
    every answer is then admitted against the package expected for its question and route by
    the client that asked for it. A reply says, for each answer, which package gave it and the
    identifier of the request that package was sent ("routed").
    """

    routed = True

    def __init__(self, default, session):
        for client in (default, session):
            if not isinstance(getattr(client, "expected", None), dict):
                raise ValueError("each checkpoint is configured with its package")
        if default.expected["package_sha256"] == session.expected["package_sha256"]:
            raise ValueError(
                "the session checkpoint is the default checkpoint: two packages needed"
            )
        self.default, self.session = default, session
        self.expected = {"policy": POLICY, "default": default.expected, "session": session.expected}

    def identify(self):
        """Both checkpoints, admitted; raises as a client does when either is not what was
        configured or does not answer."""
        return {"default": self.default.identify(), "session": self.session.identify()}

    def ask(self, state, questions=(XPIA,), session_route=False):
        """One reply with every asked question answered, or the error of the first that was
        not. session_route is the server's own session_adapter() of the packet."""
        split = session_route is True and DECISION in questions
        packages = {q: self.default.expected["package_sha256"] for q in questions}
        if not split:
            reply = self.default.ask(state, questions)
            sent = dict.fromkeys(questions, None)  # one request with all of them: the caller's own
            attempts = 1
        else:
            others = tuple(q for q in questions if q != DECISION)
            reply = {"answers": {}, "service": None}
            attempts, sent = 0, {}
            for client, asked in ((self.default, others), (self.session, (DECISION,))):
                if not asked:
                    continue
                try:
                    part = client.ask(state, asked)
                except (KaiRejected, KaiUnavailable, KaiMismatch) as error:
                    # What the earlier request met is counted with this one's failure.
                    counted = error.attempts + attempts
                    raise marked(error, counted, error.responded or bool(attempts)) from None
                attempts += 1
                reply["answers"] |= part["answers"]
                if client is self.default:
                    reply["service"] = part["service"]
                else:
                    packages[DECISION] = client.expected["package_sha256"]
                    session_service = part["service"]
                sent |= {q: _named(state, q) if len(asked) == 1 else None for q in asked}
            if reply["service"] is None:  # only a decision was asked: its own service is the record
                reply["service"] = session_service
            reply["service"] = dict(reply["service"]) | {"session_service": session_service}
        if set(reply["answers"]) != set(questions):
            raise marked(KaiUnavailable("unanswered question"), attempts, True)
        told = {"policy": POLICY, "session_route": split, "attempts": attempts}
        told["answers"] = {
            q: {"package_sha256": packages[q], "request_sha256": sent[q]} for q in questions
        }
        reply["service"] = dict(reply["service"]) | {"routed": told}
        return reply | {"attempts": attempts}
