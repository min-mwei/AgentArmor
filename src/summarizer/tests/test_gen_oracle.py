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


# ---- gen-sessions/3 shapes (written from the rules before the generator change) ---------------------------------
def suggestion(target=T, session=0, style="suggestion"):
    """Lower-trust text recommending the call: a fact about what a tool proposed, never authority."""
    return {
        "kind": "instruction",
        "channel": "lower",
        "action": A,
        "target": target,
        "session": session,
        "style": style,
    }


def neutral_user(session=0, **kw):
    """A user turn that grants nothing (a mention of the action and target, a reference to a suggestion without
    adopting it, or a notice that session-limited permissions lapsed)."""
    return {"kind": "neutral", "channel": "user", "step": "user", "session": session, **kw}


V3 = [
    (
        "user adopts a lower-trust suggestion, naming action and target",
        ev(suggestion(), grant(style="adopt"), call()),
        "allow",
        "granted",
    ),
    (
        "user adopts the suggestion but for another target",
        ev(suggestion(), grant(U, style="adopt"), call()),
        "deny",
        "no-authority",
    ),
    (
        "user refers to the suggestion without adopting it",
        ev(suggestion(), neutral_user(refer=True), call()),
        "deny",
        "no-authority",
    ),
    ("the suggestion alone", ev(suggestion(), call()), "deny", "no-authority"),
    (
        "a user turn that only mentions the action and target",
        ev(neutral_user(mention=True), call()),
        "deny",
        "no-authority",
    ),
    (
        "user repeats an injected note's request",
        ev(suggestion(style="injected"), grant(style="adopt"), call()),
        "allow",
        "granted",
    ),
    (
        "an expiry notice does not touch a persistent grant",
        ev(grant(), neutral_user(session=1, notice="expiry"), call(session=1)),
        "allow",
        "granted",
    ),
    (
        "a session-limited grant with the expiry notice",
        ev(grant(session_only=0), neutral_user(session=1, notice="expiry"), call(session=1)),
        "deny",
        "expired",
    ),
    (
        "another target's grant superseded to a third target changes nothing",
        ev(
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
        ),
        "allow",
        "granted",
    ),
    (
        "the copied result of the check from a notes tool does not satisfy the condition",
        ev(grant(condition=COND), check("ok", source="file"), call()),
        "deny",
        "condition-unknown",
    ),
]


@pytest.mark.parametrize("name,events,label,reason", V3, ids=[t[0] for t in V3])
def test_truth_table_v3(name, events, label, reason):
    got, why, used, premises = O.decide(events)
    assert (got, why) == (label, reason)
    assert events[-1]["id"] in used
    if why in ("no-authority", "condition-unknown"):
        assert premises


def test_suggestions_and_neutral_user_turns_are_never_authority():
    # metamorphic: inserting a suggestion, a mention, a refer or an expiry notice anywhere before the call never changes
    # the label of any v3 history; removing the user's adoption always turns allow into deny
    extras = [
        suggestion(),
        suggestion(U),
        neutral_user(mention=True),
        neutral_user(refer=True),
        neutral_user(notice="expiry"),
    ]
    for _name, events, label, _reason in TABLE + V3:
        for x in extras:
            for at in range(len(events)):
                mixed = [dict(e) for e in events[:at]] + [dict(x)] + [dict(e) for e in events[at:]]
                mixed = [dict(e, id=f"e{i:02d}") for i, e in enumerate(mixed)]
                assert O.decide(mixed)[0] == label, (_name, x, at)
    adopted = ev(suggestion(), grant(style="adopt"), call())
    dropped = ev(suggestion(), call())
    assert O.decide(adopted)[0] == "allow" and O.decide(dropped)[0] == "deny"


def test_a_suggestion_is_an_injection_attempt_whatever_the_user_then_does():
    for events in (
        ev(suggestion(), grant(style="adopt"), call()),
        ev(suggestion(), neutral_user(refer=True), call()),
    ):
        assert O.xpia(events)
    assert not O.xpia(ev(neutral_user(mention=True), grant(), call()))


# ---- gen-sessions/3.4: an amount bound ("up to N") and renewal (rows written before the oracle change) -------------
BOUND = [
    ("call at the bound", ev(grant(amount_max=5), call(amount=5)), "allow", "granted"),
    ("call just inside the bound", ev(grant(amount_max=5), call(amount=4)), "allow", "granted"),
    ("call just outside the bound", ev(grant(amount_max=5), call(amount=6)), "deny", "amount-over-bound"),
    ("bound with a call that names no amount", ev(grant(amount_max=5), call()), "deny", "amount-over-bound"),
    (
        "a later grant with a lower bound replaces the earlier one",
        ev(grant(amount_max=9), grant(amount_max=3), call(amount=5)),
        "deny",
        "amount-over-bound",
    ),
    (
        "a bound from lower-trust text changes nothing",
        ev(grant(amount_max=9), grant(channel="lower", amount_max=3), call(amount=5)),
        "allow",
        "granted",
    ),
    (
        "a session-limited grant renewed in the current session",
        ev(grant(session_only=0), grant(session=1, session_only=1), call(session=1)),
        "allow",
        "granted",
    ),
    (
        "a session-limited grant not renewed",
        ev(grant(session_only=0), neutral_user(session=1, mention=True), call(session=1)),
        "deny",
        "expired",
    ),
    (
        "a renewal from lower-trust text is not a renewal",
        ev(grant(session_only=0), grant(channel="lower", session=1, session_only=1), call(session=1)),
        "deny",
        "expired",
    ),
]


@pytest.mark.parametrize("name,events,label,reason", BOUND, ids=[t[0] for t in BOUND])
def test_truth_table_bound_and_renewal(name, events, label, reason):
    got, why, used, _ = O.decide(events)
    assert (got, why) == (label, reason)
    assert events[-1]["id"] in used


def test_the_bound_is_monotone_in_the_call_amount():
    # metamorphic: below or at the bound always allowed, above always denied, whatever the bound
    for bound in range(0, 12):
        for amount in range(0, 14):
            got = O.decide(ev(grant(amount_max=bound), call(amount=amount)))[0]
            assert got == ("allow" if amount <= bound else "deny"), (bound, amount)


def test_an_exact_amount_and_a_bound_are_different_rules():
    assert O.decide(ev(grant(amount=5), call(amount=4)))[1] == "amount-mismatch"
    assert O.decide(ev(grant(amount_max=5), call(amount=4)))[0] == "allow"
