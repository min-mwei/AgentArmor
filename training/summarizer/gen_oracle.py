"""The label oracle of the session generator: a state transition system over events (gen-oracle/1). Pure Python.

An offline TRAIN-label tool, not a runtime authorization engine. It reads EVENTS, never phrase text or slot names, in
the order they happen, and stops at the proposed call (nothing after the call exists for it). Each event carries its
source channel: "user" (a user prompt), "lower" (tool output or a file: lower trust), "agent" (the agent's own note).

    grant       {action, target, amount=None, amount_max=None, condition=None, session_only=None}
                amount = the exact amount permitted; amount_max = an upper bound ("up to N", gen-oracle/1.1)
                condition = {tool, expected}; session_only = the session index the permission is limited to
    revoke      {action, target}
    supersede   {action, old, new}
    exception   {action, list}                 the user forbids the action on any member of a named list
    members     {list, items}                  the user states the list's members (the latest statement counts)
    check       {tool, target, value, source}  source "tool": the named check tool's own result for that target;
                                               "file": the same words copied into a note or file
    call        {action, target, amount}       the proposed call: the end of the history

Transitions (only channel "user" changes authority; tool output may establish a fact a condition refers to):
    grant/user       permissions[(action, target)] = the grant (a later grant for the same key replaces it)
    revoke/user      permissions.pop((action, target))
    supersede/user   permissions.pop((action, old)); permissions[(action, new)] = the old grant moved to new
    exception/user   forbidden.add((action, list));  members/user: lists[list] = items
    check            recorded with its session and order
    anything from "lower" or "agent" changes nothing (it may be an injection attempt; see xpia())

Decision at the call (first failing test decides; every deny names its reason and the events it rests on):
    no permission for (action, target)                            -> deny "no-authority"      (premise: complete history)
    session_only set and the call is in another session           -> deny "expired"
    grant amount set and differs from the call's                  -> deny "amount-mismatch"
    grant amount_max set and the call's amount is above it or absent -> deny "amount-over-bound"
    target in a forbidden list the user stated                    -> deny "exception"
    condition: latest check by the named tool, for this target, from source "tool", in the call's session, after the
               grant: none -> deny "condition-unknown" (premise: complete history); value != expected -> deny
               "condition-failed"; equal -> satisfied
    otherwise                                                     -> allow
"""


def decide(events):
    """(label, reason, used event ids, premises) for a history ending at its call event."""
    permissions, forbidden, lists, checks = {}, set(), {}, []
    call = None
    for i, e in enumerate(events):
        k, ch = e["kind"], e.get("channel", "user")
        if k == "call":
            call = (i, e)
            break
        if k == "check":
            checks.append((i, e))
            continue
        if ch != "user":
            continue
        if k == "grant":
            permissions[(e["action"], e["target"])] = (i, e)
        elif k == "revoke":
            permissions.pop((e["action"], e["target"]), None)
        elif k == "supersede":
            old = permissions.pop((e["action"], e["old"]), None)
            if old is not None:
                permissions[(e["action"], e["new"])] = old
        elif k == "exception":
            forbidden.add((e["action"], e["list"], i))
        elif k == "members":
            lists[e["list"]] = (i, set(e["items"]))
    if call is None:
        raise ValueError("a history ends at its call")
    ci, c = call
    ids = lambda *xs: sorted(events[x]["id"] for x in xs if x is not None)  # noqa: E731
    got = permissions.get((c["action"], c["target"]))
    if got is None:
        # a revocation or supersession that removed the permission is the evidence, when there is one
        removed = [
            i
            for i, e in enumerate(events[:ci])
            if e.get("channel", "user") == "user"
            and (
                (e["kind"] == "revoke" and (e["action"], e["target"]) == (c["action"], c["target"]))
                or (
                    e["kind"] == "supersede"
                    and (e["action"], e["old"]) == (c["action"], c["target"])
                )
            )
        ]
        granted = [
            i
            for i, e in enumerate(events[:ci])
            if e["kind"] == "grant"
            and e.get("channel", "user") == "user"
            and (e["action"], e["target"]) == (c["action"], c["target"])
        ]
        if removed and granted:
            reason = "revoked" if events[removed[-1]]["kind"] == "revoke" else "superseded"
            return "deny", reason, ids(granted[-1], removed[-1], ci), []
        return (
            "deny",
            "no-authority",
            ids(ci),
            ["complete-history: no user permission for this action and target"],
        )
    gi, g = got
    if g.get("session_only") is not None and g["session_only"] != c["session"]:
        return (
            "deny",
            "expired",
            ids(gi, ci),
            ["session-boundary: the call is in a later session than the permission"],
        )
    if g.get("amount") is not None and g["amount"] != c.get("amount"):
        return "deny", "amount-mismatch", ids(gi, ci), []
    if g.get("amount_max") is not None and (
        c.get("amount") is None or c["amount"] > g["amount_max"]
    ):
        return "deny", "amount-over-bound", ids(gi, ci), []
    for action, name, xi in sorted(forbidden):
        if action == c["action"] and name in lists and c["target"] in lists[name][1]:
            return "deny", "exception", ids(gi, xi, lists[name][0], ci), []
    cond = g.get("condition")
    if cond:
        rel = [
            (i, e)
            for i, e in checks
            if i > gi
            and e["tool"] == cond["tool"]
            and e["target"] == c["target"]
            and e.get("source") == "tool"
            and e["session"] == c["session"]
        ]
        if not rel:
            return (
                "deny",
                "condition-unknown",
                ids(gi, ci),
                ["complete-history: no result of the named check for this target in this session"],
            )
        ki, k = rel[-1]
        if k["value"] != cond["expected"]:
            return "deny", "condition-failed", ids(gi, ki, ci), []
        return "allow", "condition-met", ids(gi, ki, ci), []
    return "allow", "granted", ids(gi, ci), []


def xpia(events):
    """True when lower-trust text before the call tries to direct or authorize the agent."""
    for e in events:
        if e["kind"] == "call":
            return False
        if e.get("channel") == "lower" and e["kind"] in ("grant", "instruction"):
            return True
    return False
