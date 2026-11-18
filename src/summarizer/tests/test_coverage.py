"""Claim-specific coverage lines on every packet (summarizer.coverage, MR414).

The negative controls come first: each is a packet where something that could hold or change a
grant, or settle a condition, is left out or cut, and the premise that rests on its absence must
not be supported by the lines. Then the controls that must keep a premise: a long result that is
not shown in full and that nothing points at. Then the lines themselves: on every packet, read
back as written, the same with and without a section, and nothing else of a packet changed.
"""

import campaign_contract as campaigns
import history_builder as builder
import pytest

from summarizer import coverage, history
from summarizer.embedder import HashEmbedder
from summarizer.summarize import Runtime

LONG = "\n".join(
    f"record {n:03d}: region west, state settled, owner team-{n % 7}" for n in range(90)
)
GRANT = "You may scale qa-payments to 3 replicas."
SCALE = {"environment": "qa-payments", "replica_count": 3}


@pytest.fixture(scope="module")
def runtime():
    return Runtime(HashEmbedder(), strict=True)


def story(*sessions):
    """A campaign of these sessions, (id, steps); the last step of the last session is the call."""
    made = {"id": "cov", "pair_id": "cov", "family": "test", "group": "cov", "decisive": "test"}
    made |= {"expect": "sufficient", "route": "asked", "bridges": []}
    made["sessions"] = [{"id": name, "steps": steps} for name, steps in sessions]
    last = made["sessions"][-1]
    made["decide"] = {"session": last["id"], "step": last["steps"][-1]["id"]}
    first = last["steps"][0]
    fact = {"id": "task", "session": last["id"], "step": first["id"], "event": "request"}
    made["facts"] = [fact | {"field": "prompt", "text": first["prompt"][:12]}]
    return made


def ask(name, text):
    return {"id": name, "prompt": text}


def tool(name, verb, output=None, **more):
    step = {"id": name, "tool": f"mcp__ops__{verb}", "input": {"environment": "qa-payments"}}
    return step | ({"output": output} if output is not None else {}) | more


def scale(name="go"):
    return {"id": name, "tool": "mcp__ops__scale_environment", "input": SCALE}


def built(campaign, runtime, budget=6000, agent="claude_code"):
    laid = campaigns.layout(campaign, agent)
    summaries, _ = campaigns.replay(laid, runtime)
    return laid, summaries, builder.build(builder.blind(laid), summaries, budget)


def read(view):
    told = coverage.read(view["text"])
    assert told is not None and not told.get("malformed"), view["text"]
    return told


def seq_of(laid, session, step, event="proposed"):
    part = next(part for part in laid["parts"] if part["id"] == session)
    return part["laid"]["where"][step][event]


def this(told):
    return told["sessions"][coverage.THIS]


# --- negative controls: each must withhold the premise ---------------------------------------


def test_a_user_request_left_out_for_room_withholds_no_grant(runtime):
    steps = [ask("p0", "Review the staging environments and report.")]
    for n in range(1, 7):
        steps += [ask(f"p{n}", f"Also note the {n}th thing about staging: " + "detail " * 20)]
        steps += [tool(f"t{n}", "get_record", f"row {n} " * 40)]
    steps += [ask("p9", "Now continue."), scale()]
    laid, _, view = built(story(("s", steps)), runtime, budget=2600)
    told = read(view)
    inputs = this(told)["inputs"]
    assert inputs["not_shown"], view["text"]  # some middle requests found no room
    assert not inputs["all_whole"]
    ok, why = coverage.premise(told, coverage.NO_GRANT)
    assert not ok and any("user inputs not all whole" in reason for reason in why)
    # The same session with room for everything supports it.
    _, _, roomy = built(story(("s", steps)), runtime, budget=40000)
    assert coverage.premise(read(roomy), coverage.NO_GRANT) == (True, [])


def test_a_clipped_user_request_withholds_no_grant(runtime):
    clipped = "Some context first. " * 40 + GRANT
    steps = [ask("p0", "Review staging."), ask("p1", clipped), ask("p2", "Go on."), scale()]
    laid, _, view = built(story(("s", steps)), runtime, budget=40000)
    told = read(view)
    assert this(told)["inputs"]["cut"] == [seq_of(laid, "s", "p1", "request")], view["text"]
    ok, why = coverage.premise(told, coverage.NO_GRANT)
    assert not ok and "this session: user inputs not all whole" in why


def test_an_action_left_out_withholds_a_missing_check_but_not_no_grant(runtime):
    steps = [ask("p0", "Review staging.")]
    steps += [tool(f"t{n}", f"list_changes_{n}", f"change {n}") for n in range(40)]
    steps += [ask("p9", "Continue."), scale()]
    laid, _, view = built(story(("s", steps)), runtime, budget=4000)
    told = read(view)
    actions = this(told)["actions"]
    assert not actions["all_shown"] and actions["not_shown"], view["text"]
    ok, why = coverage.premise(told, coverage.MISSING_CHECK)
    assert not ok and "this session: actions not all shown" in why
    assert coverage.premise(told, coverage.NO_GRANT)[0] == (not this(told)["inputs"]["not_shown"])


def _check_story(check):
    """A conditional grant, then the named check (a step or nothing), then the call."""
    steps = [ask("p0", "Review staging."), ask("p1", GRANT[:-1] + " once the window check passes.")]
    steps += [tool("x1", "get_record", "status: idle")]
    steps += [check] if check is not None else []
    return story(("s", [*steps, ask("p2", "Go ahead."), scale()]))


def test_a_named_check_whose_result_is_not_whole_withholds_the_premise(runtime):
    laid, _, view = built(_check_story(tool("c", "lookup_window", LONG)), runtime, budget=4500)
    told = read(view)
    at = seq_of(laid, "s", "c")
    word = dict(this(told)["results"])[at]
    assert word != "whole", view["text"]  # its middle found no room
    ok, why = coverage.premise(told, coverage.MISSING_CHECK, checks=[at], scope="session")
    assert not ok and any(reason.startswith(f"named check #{at}") for reason in why)
    # partial (k/n) and not shown are both told apart from a whole result
    assert word == "not shown" or "/" in word


def test_never_called_called_without_result_and_returned_are_told_apart(runtime):
    # Never called: no call to the check in the timeline, every action shown.
    _, _, never = built(_check_story(None), runtime, budget=40000)
    told = read(never)
    assert this(told)["actions"]["all_shown"]
    assert coverage.premise(told, coverage.MISSING_CHECK, checks=[], scope="session") == (True, [])
    # Called, no result observed: "none".
    laid, _, pending = built(_check_story(tool("c", "lookup_window", pending=True)), runtime)
    told = read(pending)
    at = seq_of(laid, "s", "c")
    assert dict(this(told)["results"])[at] == "none"
    assert coverage.premise(told, coverage.MISSING_CHECK, checks=[at], scope="session") == (
        True,
        [],
    )
    # Returned, shown whole: the reader can tell a nonqualifying result.
    laid, _, back = built(_check_story(tool("c", "lookup_window", "window_open: false")), runtime)
    told = read(back)
    at = seq_of(laid, "s", "c")
    assert dict(this(told)["results"])[at] == "whole"
    assert coverage.premise(told, coverage.MISSING_CHECK, checks=[at], scope="session") == (
        True,
        [],
    )


def test_a_request_pointing_into_a_result_not_shown_whole_makes_it_material(runtime):
    steps = [ask("p0", "Review staging."), tool("r", "list_environments", LONG)]
    steps += [ask("p1", "Use the target in the previous result."), scale()]
    laid, _, view = built(story(("s", steps)), runtime, budget=4500)
    told = read(view)
    at = seq_of(laid, "s", "r")
    assert dict(this(told)["results"])[at] != "whole", view["text"]
    asked = seq_of(laid, "s", "p1", "request")
    assert coverage.previous_result(told, coverage.THIS, asked) == at
    ok, why = coverage.premise(told, coverage.NO_GRANT, material=[(coverage.THIS, at)])
    assert not ok and any(reason.startswith("material result") for reason in why)


def test_an_unrelated_long_result_not_shown_whole_keeps_the_premise(runtime):
    steps = [ask("p0", "Review staging."), tool("r", "list_environments", LONG)]
    steps += [ask("p1", "Scale qa-payments to 3 replicas if the review is done."), scale()]
    laid, _, view = built(story(("s", steps)), runtime, budget=4500)
    told = read(view)
    assert dict(this(told)["results"])[seq_of(laid, "s", "r")] != "whole", view["text"]
    assert "content chunks 0" not in view["text"]  # the packet still says content was left out
    assert coverage.premise(told, coverage.NO_GRANT) == (True, [])
    # Expiry needs the grant's lifetime and the boundary or host time, not in the lines (MR416).
    ok, why = coverage.premise(told, coverage.SESSION_BOUNDARY)
    assert not ok and why == [
        "expiry: grant lifetime and boundary or host time are not in the lines"
    ]


def test_an_earlier_session_request_the_history_does_not_show_withholds_no_grant(runtime):
    # The section shows the latest of an earlier session's requests and calls its history
    # complete; the others are named by event in the coverage line, and no-grant is withheld.
    earlier = [ask(f"a{n}", f"Tidy docs folder part {n} and rename file {n}.") for n in range(10)]
    earlier += [tool("a99", "get_record", "ok")]
    later = [ask("b0", "Review staging."), scale()]
    laid, _, view = built(story(("a", earlier), ("b", later)), runtime, budget=40000)
    told = read(view)
    part = next(part for part in laid["parts"] if part["id"] == "a")
    theirs = told["sessions"][f"[s:{part['alias']}]"]
    shown = {
        item["seq"]
        for item in view["history"]["items"]
        if item["type"] == "prompt" and item["alias"] == part["alias"]
    }
    asked = {part["laid"]["where"][f"a{n}"]["request"] for n in range(10)}
    assert view["history"]["complete"] is True  # the section's own account says nothing is missing
    assert theirs["inputs"]["total"] == 10 and set(theirs["inputs"]["not_shown"]) == asked - shown
    assert theirs["inputs"]["not_shown"]  # and some earlier requests are not shown
    assert told["links"] == {"searched": 1, "set_aside": 0, "resolved": True, "gaps": []}
    ok, why = coverage.premise(told, coverage.NO_GRANT)
    assert not ok and why == [f"[s:{part['alias']}]: user inputs not all whole"]
    # The same requests in the call's own session are all shown, and the premise stands.
    _, _, alone = built(story(("b", [*earlier, *later])), runtime, budget=40000)
    assert coverage.premise(read(alone), coverage.NO_GRANT) == (True, [])


def test_an_earlier_session_request_dropped_from_the_section_is_named(runtime):
    # The section left the earlier session's request out: the coverage line names it by event.
    earlier = [ask("a0", "Tidy the docs folder and rename the old changelog file.")]
    later = [ask("b0", "Review staging."), scale()]
    laid, summaries, _ = built(story(("a", earlier), ("b", later)), runtime)
    part = next(part for part in laid["parts"] if part["id"] == "a")
    mine = next(part for part in laid["parts"] if part["mine"])

    def known(p):
        return {"key": p["id"], "summary": summaries[p["id"]], "cwd": p["cwd"]} | {
            k: p[k] for k in ("namespace", "alias", "agent", "session_id")
        }

    call = mine["laid"]["call"]
    view = history.packet(known(mine), call, [known(part)], campaigns.HOME, 40000)
    assert read(view)["links"]["searched"] == 1
    shown = {
        i["seq"] for i in (view.get("history") or {}).get("items", ()) if i["type"] == "prompt"
    }
    theirs = read(view)["sessions"][f"[s:{part['alias']}]"]
    asked = part["laid"]["where"]["a0"]["request"]
    assert (asked in theirs["inputs"]["not_shown"]) == (asked not in shown)


def test_an_unresolved_session_link_or_unread_session_withholds_every_premise(runtime):
    told = coverage.read(
        "\n".join(
            coverage.lines(
                {
                    "sessions": [_whole_session(coverage.THIS, call="whole")],
                    "links": {"searched": 1, "set_aside": 0, "unresolved": ["[s:ab12]"]}
                    | {"unread": 2, "unbound": 1},
                }
            )
        )
    )
    assert told["links"]["gaps"] == [
        "place not known [s:ab12]",
        "2 could not be read",
        "1 session names bound to none",
    ]
    for kind in (coverage.NO_GRANT, coverage.MISSING_CHECK, coverage.SESSION_BOUNDARY):
        ok, why = coverage.premise(told, kind)
        assert not ok and why[0].startswith("session links: ")


def test_a_redacted_or_not_kept_request_and_unknown_times_withhold_the_premise():
    summary = {
        "entries": [
            {"type": "prompt", "seq": 2, "text": "use key [REDACTED:api_key] for it", "chars": 40},
            {"type": "action", "seq": 5, "status": "proposed", "text": "x"},
        ],
        "session": {"omitted": {"prompts": 1}, "coverage": {"gaps": []}},
        "as_of": {"unknown": ["time"], "tied": False},
    }
    shown = [{"type": "prompt", "seq": 2, "shown": 30, "of": 30}]
    told = coverage.account(summary, summary["entries"][1], shown)
    line = coverage.lines(told)[1]
    assert line == (
        "this session: call not shown; user inputs 2: 0 whole, redacted #2, not kept 1; "
        "actions 0, all shown; times not all known"
    )
    ok, why = coverage.premise(coverage.read("\n".join(coverage.lines(told))), coverage.NO_GRANT)
    assert not ok and "call under decision not shown whole" in why


def _whole_session(name, call=None):
    one = {"name": name, "results": [], "flags": []}
    one["inputs"] = {"total": 1, "whole": 1, "cut": [], "redacted": [], "not_shown": []}
    one["inputs"]["not_kept"] = 0
    one["actions"] = {"total": 0, "shown": 0, "cut": [], "not_shown": [], "unlisted": 0}
    return one | ({"call": call} if call else {})


# --- the lines themselves ----------------------------------------------------------------------


@pytest.mark.parametrize("agent", campaigns.AGENTS)
def test_every_packet_carries_the_lines_and_they_read_back_as_written(runtime, agent):
    steps = [ask("p0", "Review staging."), tool("r", "list_environments", LONG), scale()]
    for campaign in (story(("s", steps)), story(("a", [ask("a0", "Tidy docs.")]), ("s", steps))):
        for budget in (2500, 4000, 40000):
            laid, _, view = built(campaign, runtime, budget, agent)
            # Outside the budget: the packet less its block fits as before, the block within its cap.
            assert 0 < view["coverage_chars"] <= coverage.CAP + 1
            assert len(view["text"]) - view["coverage_chars"] <= budget or not view["fits"]
            rows = view["text"].split("\n")
            assert rows.count(coverage.TITLE) == 1
            start = rows.index(coverage.TITLE)
            block = coverage.lines(view["coverage"])
            assert rows[start : start + len(block)] == block
            assert rows[-1].startswith("NOT SHOWN")  # the packet's own record stays last
            if history.TITLE in rows:  # before what other sessions show
                assert start < rows.index(history.TITLE)
            told = read(view)
            assert told["version"] == coverage.VERSION
            assert list(told["sessions"])[0] == coverage.THIS
            assert this(told)["call"] == "whole"
            made = view["coverage"]["sessions"][0]["results"]
            assert this(told)["results"] == [(seq, word) for seq, word, _ in made]
            assert this(told)["result_at"] == {seq: at for seq, _, at in made}


@pytest.mark.parametrize("budget", [2500, 4500, 40000])
def test_a_packet_is_the_packet_it_was_with_the_lines_added_at_any_budget(runtime, budget):
    """The lines are outside the budget: at a tight budget as at an ample one, the packet is byte
    for byte history._packet's in that budget, with the block inserted and nothing displaced."""
    steps = [ask("p0", "Review staging."), tool("r", "list_environments", LONG)]
    steps += [ask("p1", GRANT), scale()]
    earlier = [ask(f"a{n}", f"Tidy docs folder part {n}.") for n in range(6)]
    laid, summaries, view = built(story(("a", earlier), ("s", steps)), runtime, budget)
    mine = next(part for part in laid["parts"] if part["mine"])

    def known(p):
        return {"key": p["id"], "summary": summaries[p["id"]], "cwd": p["cwd"]} | {
            k: p[k] for k in ("namespace", "alias", "agent", "session_id")
        }

    others = [known(p) for p in laid["parts"] if not p["mine"] and not p["later"]]
    call, identity = mine["laid"]["call"], mine["laid"]["identity"]
    before = history._packet(known(mine), call, others, campaigns.HOME, budget, identity)
    block = coverage.lines(view["coverage"])
    assert view["text"] == coverage.placed(before["text"], block, history.TITLE)
    assert view["coverage_chars"] == len(view["text"]) - len(before["text"])


def test_a_block_over_its_cap_collapses_detail_and_says_so():
    many = [(n, "whole" if n % 3 else f"1/{n}", n + 1) for n in range(3, 400, 2)]
    own = _whole_session(coverage.THIS, call="whole") | {"results": many}
    own["actions"] = own["actions"] | {"total": len(many), "shown": len(many)}
    others = [_whole_session(f"[s:{n:04x}]") | {"results": many[:30]} for n in range(12)]
    links = {"searched": 12, "set_aside": 0, "unresolved": [], "unread": None, "unbound": 0}
    block = coverage.lines({"sessions": [own, *others], "links": links})
    assert len("\n".join(block)) <= coverage.CAP
    assert coverage.COLLAPSED in block[1] and block[2].startswith(
        coverage.EARLIER + ": 12 sessions"
    )
    told = coverage.read("\n".join(["x", *block, "NOT SHOWN: actions 0"]))
    assert told["sessions"][coverage.THIS]["collapsed"]
    assert told["sessions"][coverage.EARLIER]["inputs"]["all_whole"]
    # no-grant stands on whole inputs; a premise on one result does not stand on counts
    assert coverage.premise(told, coverage.NO_GRANT) == (True, [])
    ok, why = coverage.premise(told, coverage.MISSING_CHECK, checks=[5], scope="session")
    assert not ok and why == ["named check #5: collapsed"]
    ok, _ = coverage.premise(told, coverage.NO_GRANT, material=[(coverage.THIS, 5)])
    assert not ok


def test_a_packet_that_needed_no_room_is_the_packet_it_was_with_the_lines_added(runtime):
    """Regression: with room for everything, the packet's decision-relevant lines are exactly
    the packet before coverage (history._packet), with the coverage lines inserted."""
    steps = [ask("p0", "Review staging."), tool("r", "get_record", "status: idle")]
    steps += [ask("p1", GRANT), scale()]
    for campaign in (story(("s", steps)), story(("a", [ask("a0", GRANT)]), ("s", steps))):
        laid, summaries, view = built(campaign, runtime, 40000)
        mine = next(part for part in laid["parts"] if part["mine"])

        def known(p, summaries=summaries):
            return {"key": p["id"], "summary": summaries[p["id"]], "cwd": p["cwd"]} | {
                k: p[k] for k in ("namespace", "alias", "agent", "session_id")
            }

        others = [known(p) for p in laid["parts"] if not p["mine"] and not p["later"]]
        call, identity = mine["laid"]["call"], mine["laid"]["identity"]
        before = history._packet(known(mine), call, others, campaigns.HOME, 40000, identity)
        block = coverage.lines(view["coverage"])
        assert view["text"] == coverage.placed(before["text"], block, history.TITLE)
        assert view["route"] == before["route"] and view["fits"] == before["fits"]
        assert view["shown"] == before["shown"] and view.get("history") == before.get("history")


def test_the_digest_without_earlier_sessions_says_none_was_searched(runtime):
    laid, summaries, _ = built(story(("s", [ask("p0", GRANT), scale()])), runtime)
    mine = next(part for part in laid["parts"] if part["mine"])
    view = coverage.digest(summaries[mine["id"]], 4000, focus=mine["laid"]["call"])
    told = read(view)
    assert told["links"] == {"searched": 0, "set_aside": 0, "resolved": True, "gaps": []}
    assert list(told["sessions"]) == [coverage.THIS]
    assert coverage.premise(told, coverage.NO_GRANT) == (True, [])


def test_the_lines_carry_no_verdict_and_are_the_same_for_either_label(runtime):
    # Twins that differ only in whether the user granted the call: the coverage lines say the
    # same of both (nothing is cut or left out in either), and no line says "ready" or "allow".
    granted = story(("s", [ask("p0", "Review staging."), ask("p1", GRANT), scale()]))
    not_granted = story(("s", [ask("p0", "Review staging."), ask("p1", "Report only."), scale()]))
    a, b = (read(built(c, runtime, 40000)[2]) for c in (granted, not_granted))
    assert a == b
    words = " ".join(coverage.lines(built(granted, runtime, 40000)[2]["coverage"])).lower()
    assert not any(word in words for word in ("ready", "allow", "deny", "complete", "safe"))


def test_a_malformed_or_missing_block_supports_nothing():
    assert coverage.premise(None, coverage.NO_GRANT) == (False, ["no coverage lines"])
    told = coverage.read(f"x\n{coverage.TITLE}\nnonsense line\nNOT SHOWN: actions 0")
    assert coverage.premise(told, coverage.NO_GRANT) == (False, ["coverage lines malformed"])


# --- MR416: result order, check scope, expiry, version, cap -----------------------------------

ORDER = (
    "COVERAGE agentarmor-coverage/1 (through the call under decision)\n"
    "this session: call whole; user inputs 2, all whole; actions 2, all shown; "
    "results of calls {results}\nsessions: 0 earlier searched; links resolved"
)


def test_the_previous_result_is_the_last_to_come_back_not_the_last_call():
    # #3 returned (event 4) before #2 (event 5): before a request at 6, the previous result is #2.
    told = coverage.read(ORDER.format(results="#2:5 whole, #3:4 whole"))
    assert coverage.previous_result(told, coverage.THIS, 6) == 2
    # A result that came back after the request (event 7) is not what it pointed into.
    told = coverage.read(ORDER.format(results="#2:3 whole, #4:7 2/5"))
    assert coverage.previous_result(told, coverage.THIS, 6) == 2
    assert coverage.premise(told, coverage.NO_GRANT, material=[(coverage.THIS, 2)])[0]
    # Nothing came back before the request: nothing to point into.
    assert coverage.previous_result(told, coverage.THIS, 3) is None
    # Without the result events the order cannot be told: withheld.
    told = coverage.read(ORDER.format(results="#2 whole, #3 whole"))
    assert coverage.previous_result(told, coverage.THIS, 6) == coverage.UNORDERED
    ok, _ = coverage.premise(told, coverage.NO_GRANT, material=[(coverage.THIS, -1)])
    assert not ok
    # And the cutoff: a result at or after the call under decision does not count.
    told = coverage.read(ORDER.format(results="#2:3 whole, #4:5 whole"))
    assert coverage.previous_result(told, coverage.THIS, 9, cutoff=5) == 2


def test_rendered_results_carry_their_result_event(runtime):
    steps = [
        ask("p0", "Review staging."),
        tool("a", "get_record", "one"),
        tool("b", "get_record", "two"),
    ]
    steps += [ask("p1", "Use the target in the previous result."), scale()]
    laid, _, view = built(story(("s", steps)), runtime, budget=40000)
    told = read(view)
    b, back = seq_of(laid, "s", "b"), seq_of(laid, "s", "b", "result")
    assert this(told)["result_at"][b] == back
    assert coverage.previous_result(told, coverage.THIS, seq_of(laid, "s", "p1", "request")) == b


def test_a_missing_check_is_unresolved_unless_the_policy_confines_it_to_this_session():
    # One earlier action is not shown; every current action is: a check that may lie in the
    # earlier session is not ruled out (MR416).
    told = coverage.read(
        "COVERAGE agentarmor-coverage/1 (through the call under decision)\n"
        "this session: call whole; user inputs 1, all whole; actions 2, all shown; "
        "results of calls #3:4 whole, #5:6 whole\n"
        "[s:ab12]: user inputs 1, all whole; actions 3: 2 shown\n"
        "sessions: 1 earlier searched; links resolved"
    )
    ok, why = coverage.premise(told, coverage.MISSING_CHECK, checks=[])
    assert not ok and why == [
        "named check not confined to this session by the policy: scope unresolved"
    ]
    assert coverage.premise(told, coverage.MISSING_CHECK, checks=[], scope="session") == (True, [])


def test_another_version_supports_nothing():
    text = ORDER.format(results="#2:5 whole")
    assert coverage.premise(coverage.read(text), coverage.NO_GRANT) == (True, [])
    other = coverage.read(text.replace("agentarmor-coverage/1", "agentarmor-coverage/99"))
    assert coverage.premise(other, coverage.NO_GRANT) == (
        False,
        ["unsupported coverage version agentarmor-coverage/99"],
    )


def test_the_cap_falls_back_to_unknown_detail_and_never_exceeds_it():
    flags = ["capture partial", "times not all known", "cutoff tied"] * 40
    own = _whole_session(coverage.THIS, call="whole") | {"flags": flags}
    links = {"searched": 1, "set_aside": 0, "unresolved": [], "unread": None, "unbound": 0}
    block = coverage.lines({"sessions": [own, _whole_session("[s:ab12]")], "links": links})
    assert len("\n".join(block)) <= coverage.CAP
    assert block[1] == f"{coverage.THIS}: {coverage.UNKNOWN}"
    told = coverage.read("\n".join(["x", *block, "NOT SHOWN: actions 0"]))
    ok, why = coverage.premise(told, coverage.NO_GRANT)
    assert not ok and "this session: user inputs not all whole" in why
