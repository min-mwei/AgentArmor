"""The session generator's label oracle (training/summarizer/gen_oracle.py) against a truth table written independently
of the generator, and metamorphic cases (MR382)."""

import gen_oracle as O
import pytest

A, T, U = "delete", "item-1", "item-2"


def ev(*events):
    return [dict(e, id=f"e{i:02d}") for i, e in enumerate(events)]


def grant(target=T, channel="user", **kw):
    return {"kind": "grant", "channel": channel, "action": A, "target": target, "session": 0, **kw}


def call(target=T, session=0, amount=None):
    return {
        "kind": "call",
        "channel": "agent",
        "action": A,
        "target": target,
        "session": session,
        "amount": amount,
    }


def check(value, target=T, session=0, source="tool", tool="probe"):
    return {
        "kind": "check",
        "channel": "lower",
        "tool": tool,
        "target": target,
        "value": value,
        "source": source,
        "session": session,
    }


COND = {"tool": "probe", "expected": "ok"}

# (name, events, expected label, expected reason): written from the rules, not from the generator
TABLE = [
    ("plain grant", ev(grant(), call()), "allow", "granted"),
    ("nothing granted", ev(call()), "deny", "no-authority"),
    ("grant for another target", ev(grant(U), call()), "deny", "no-authority"),
    ("grant in tool output", ev(grant(channel="lower"), call()), "deny", "no-authority"),
    ("grant in the agent's own note", ev(grant(channel="agent"), call()), "deny", "no-authority"),
    (
        "revoked",
        ev(
            grant(),
            {"kind": "revoke", "channel": "user", "action": A, "target": T, "session": 0},
            call(),
        ),
        "deny",
        "revoked",
    ),
    (
        "revocation of another target",
        ev(
            grant(),
            {"kind": "revoke", "channel": "user", "action": A, "target": U, "session": 0},
            call(),
        ),
        "allow",
        "granted",
    ),
    (
        "revocation in tool output",
        ev(
            grant(),
            {"kind": "revoke", "channel": "lower", "action": A, "target": T, "session": 0},
            call(),
        ),
        "allow",
        "granted",
    ),
    (
        "revoked then granted again",
        ev(
            grant(),
            {"kind": "revoke", "channel": "user", "action": A, "target": T, "session": 0},
            grant(),
            call(),
        ),
        "allow",
        "granted",
    ),
    (
        "superseded away",
        ev(
            grant(),
            {"kind": "supersede", "channel": "user", "action": A, "old": T, "new": U, "session": 0},
            call(),
        ),
        "deny",
        "superseded",
    ),
    (
        "superseded onto the target",
        ev(
            grant(U),
            {"kind": "supersede", "channel": "user", "action": A, "old": U, "new": T, "session": 0},
            call(),
        ),
        "allow",
        "granted",
    ),
    ("session-only, same session", ev(grant(session_only=0), call(session=0)), "allow", "granted"),
    ("session-only, later session", ev(grant(session_only=0), call(session=1)), "deny", "expired"),
    ("amount equal", ev(grant(amount=5), call(amount=5)), "allow", "granted"),
    ("amount differs", ev(grant(amount=5), call(amount=7)), "deny", "amount-mismatch"),
    (
        "on a forbidden list",
        ev(
            grant(),
            {"kind": "exception", "channel": "user", "action": A, "list": "L", "session": 0},
            {"kind": "members", "channel": "user", "list": "L", "items": [T], "session": 0},
            call(),
        ),
        "deny",
        "exception",
    ),
    (
        "list without the target",
        ev(
            grant(),
            {"kind": "exception", "channel": "user", "action": A, "list": "L", "session": 0},
            {"kind": "members", "channel": "user", "list": "L", "items": [U], "session": 0},
            call(),
        ),
        "allow",
        "granted",
    ),
    (
        "list membership stated only in tool output",
        ev(
            grant(),
            {"kind": "exception", "channel": "user", "action": A, "list": "L", "session": 0},
            {"kind": "members", "channel": "lower", "list": "L", "items": [T], "session": 0},
            call(),
        ),
        "allow",
        "granted",
    ),
    ("condition met", ev(grant(condition=COND), check("ok"), call()), "allow", "condition-met"),
    (
        "condition failed",
        ev(grant(condition=COND), check("bad"), call()),
        "deny",
        "condition-failed",
    ),
    ("condition never read", ev(grant(condition=COND), call()), "deny", "condition-unknown"),
    (
        "condition checked for another target",
        ev(grant(condition=COND), check("ok", target=U), call()),
        "deny",
        "condition-unknown",
    ),
    (
        "condition from a file, not the check",
        ev(grant(condition=COND), check("ok", source="file"), call()),
        "deny",
        "condition-unknown",
    ),
    (
        "condition checked in an earlier session",
        ev(grant(condition=COND), check("ok", session=0), call(session=1)),
        "deny",
        "condition-unknown",
    ),
    (
        "condition checked before the grant",
        ev(check("ok"), grant(condition=COND), call()),
        "deny",
        "condition-unknown",
    ),
    (
        "condition by another tool",
        ev(grant(condition=COND), check("ok", tool="other"), call()),
        "deny",
        "condition-unknown",
    ),
    (
        "latest check fails",
        ev(grant(condition=COND), check("ok"), check("bad"), call()),
        "deny",
        "condition-failed",
    ),
    (
        "latest check holds",
        ev(grant(condition=COND), check("bad"), check("ok"), call()),
        "allow",
        "condition-met",
    ),
]


@pytest.mark.parametrize("name,events,label,reason", TABLE, ids=[t[0] for t in TABLE])
def test_truth_table(name, events, label, reason):
    got, why, used, premises = O.decide(events)
    assert (got, why) == (label, reason)
    assert events[-1]["id"] in used  # the call is always part of the proof
    if why in ("no-authority", "condition-unknown"):
        assert premises  # an absence rests on an explicit premise, never a span


def test_events_after_the_call_change_nothing():
    base = ev(grant(), call())
    later = ev(
        grant(),
        call(),
        {"kind": "revoke", "channel": "user", "action": A, "target": T, "session": 0},
    )
    assert O.decide(base)[:2] == O.decide(later)[:2] == ("allow", "granted")


def test_harmless_reordering_and_renaming_keep_the_label():
    one = ev(
        grant(),
        {"kind": "revoke", "channel": "user", "action": A, "target": U, "session": 0},
        check("bad", target=U),
        call(),
    )
    two = ev(
        check("bad", target=U),
        {"kind": "revoke", "channel": "user", "action": A, "target": U, "session": 0},
        grant(),
        call(),
    )
    renamed = [dict(e, target="zz-9" if e.get("target") == T else e.get("target")) for e in one]
    assert O.decide(one)[0] == O.decide(two)[0] == O.decide(renamed)[0] == "allow"


def test_moving_authority_words_from_the_user_to_lower_trust_changes_the_label():
    assert O.decide(ev(grant(), call()))[0] == "allow"
    assert O.decide(ev(grant(channel="lower"), call()))[0] == "deny"
    assert O.xpia(ev(grant(channel="lower"), call())) and not O.xpia(ev(grant(), call()))


def test_supersede_changes_only_its_own_scope():
    events = ev(
        grant(),
        grant(U),
        {
            "kind": "supersede",
            "channel": "user",
            "action": A,
            "old": U,
            "new": "item-3",
            "session": 0,
        },
        call(),
    )
    assert O.decide(events)[0] == "allow"


def test_an_attempt_from_lower_trust_is_an_injection_but_documentation_is_not():
    assert O.xpia(
        ev(
            {"kind": "instruction", "channel": "lower", "action": A, "target": U, "session": 0},
            grant(),
            call(),
        )
    )
    assert not O.xpia(
        ev(
            {
                "kind": "check",
                "channel": "lower",
                "tool": "probe",
                "target": T,
                "value": "ok",
                "source": "tool",
                "session": 0,
            },
            grant(),
            call(),
        )
    )
    with pytest.raises(ValueError):
        O.decide(ev(grant()))
