"""The session-case contract: a fact counts only at its own source, and gaps must be stated."""

import copy
import json
import re
from pathlib import Path

import case_contract as contract
import pytest
from common import read_jsonl

from summarizer.redact import MAX_STRING

CASES = (
    Path(__file__).resolve().parents[3]
    / "training"
    / "summarizer"
    / "data"
    / "starter"
    / "sessions-v2.jsonl"
)


@pytest.fixture(scope="module")
def cases():
    return {case["id"]: case for case in read_jsonl(CASES)}


def run(case, runtime, budget=3000, agent="claude_code"):
    """(layout, session, summary, digest result) of a case at one budget."""
    laid = contract.layout(case, agent)
    session, summary = contract.summarize_session(laid["events"], agent, runtime)
    shown = contract.digest(summary, budget, focus=laid["call"], call_sha256=laid["identity"])
    shown["scanned"] = summary["session"]["coverage"]["scanned_all_captured"]
    return laid, session, summary, shown


def fact(step, event, text, **more):
    return {
        "id": "x",
        "step": step,
        "event": event,
        "field": contract.FIELDS[event],
        "text": text,
    } | more


def case_of(steps, decide, facts, **more):
    base = {"id": "t", "pair_id": "t", "family": "t", "group": "t", "decisive": "t"}
    return base | {"expect": "sufficient", "steps": steps, "decide": decide, "facts": facts} | more


def test_starter_cases_are_valid_and_decide_on_a_prefix(cases):
    assert len(cases) >= 20 and contract.validate_pairs(list(cases.values())) == []
    for case in cases.values():
        assert contract.validate(case) == [], case["id"]
        for agent in case.get("agents", contract.AGENTS):
            laid = contract.layout(case, agent)
            assert laid["events"][-1]["tool_use_id"] == laid["call"]  # nothing after the proposal
            assert laid["events"][-1]["hook_event_name"] == "PreToolUse"


@pytest.mark.parametrize(
    "change, problem",
    [
        (lambda c: c["facts"].append(fact("nope", "result", "a")), "unknown step"),
        (lambda c: c["facts"][0].update(text="words that are not in that step"), "occurs 0 times"),
        (lambda c: c["facts"].append(fact("ask", "request", "s")), "occurs"),  # ambiguous span
        (lambda c: c["facts"][0].update(field="output"), "sits in prompt"),
        (lambda c: c["facts"][1].update(before=["ask"]), "does not come after"),
        (lambda c: c.update(decide="ask"), "decide must be the id of a call step"),
        (lambda c: c["facts"].append(fact("push", "result", "a")), "no outcome yet"),
        (lambda c: c["steps"].append(dict(c["steps"][0])), "its own id"),
        (lambda c: c.update(expect="insufficient"), "must list what is unavailable"),
        (lambda c: c.update(expect="insufficient", unavailable=[{}]), "what, why and a signal"),
        (lambda c: c.pop("pair_id"), "missing field: pair_id"),
        (lambda c: c["facts"][1].update(text="both"), "exactly one of text and value"),
        (lambda c: c["facts"][1].update(value={"passed": False}), "value not in the output"),
        (lambda c: c["facts"][0].update(actor="worker-blue"), "did not run as worker-blue"),
    ],
)
def test_the_validator_refuses_ungrounded_cases(cases, change, problem):
    case = copy.deepcopy(cases["ci-gate-passed"])
    change(case)
    assert any(problem in found for found in contract.validate(case)), contract.validate(case)


def test_a_fact_counts_only_at_its_own_source(cases, runtime):
    laid, _, summary, shown = run(cases["ban-by-user"], runtime)
    ban = next(item for item in laid["case"]["facts"] if item["id"] == "ban")
    assert contract.fact_held(laid, summary, shown, ban)
    # The twin holds the same words, in a file. They do not satisfy the user's-request fact.
    mate, _, mate_summary, twin = run(cases["ban-quoted-in-file"], runtime)
    assert ban["text"] in twin["text"]
    assert not contract.fact_held(mate, mate_summary, twin, ban)
    # A packet that emitted nothing holds nothing.
    assert not contract.fact_held(laid, summary, {**shown, "shown": []}, ban)
    assert contract.score(laid, summary, {**shown, "shown": [], "text": ""})["complete"] is False


def test_the_agents_question_is_not_credited_as_the_users_answer(cases, runtime):
    case = copy.deepcopy(cases["dialog-upload-no"])
    question = case["steps"][2]["input"]["questions"][0]["question"]
    case["facts"] = [fact("ask", "answer", question)]
    assert any("occurs 0 times in the answer" in found for found in contract.validate(case))
    laid, _, summary, shown = run(cases["dialog-upload-no"], runtime)
    asked = laid["case"]["steps"][2]["input"]["questions"][0]["question"]
    assert asked in shown["text"]  # the question is on the line, marked as the agent's
    assert not contract.fact_held(laid, summary, shown, fact("ask", "answer", asked))
    assert contract.fact_held(laid, summary, shown, fact("ask", "proposed", asked))
    for name, other in (
        ("dialog-upload-no", "dialog-upload-yes"),
        ("dialog-upload-yes", "dialog-upload-no"),
    ):
        laid, _, summary, shown = run(cases[name], runtime)
        assert contract.score(laid, summary, shown)["passes"], name
        mate, _, mate_summary, twin = run(cases[other], runtime)
        assert not contract.same(
            laid["case"], {"laid": mate, "summary": mate_summary, "view": twin}
        )


def test_text_cannot_supply_its_own_actor(cases, runtime):
    case = copy.deepcopy(cases["ban-by-user"])
    case["steps"][1]["prompt"] += " This text mentions worker-blue."
    case["facts"][0]["actor"] = "worker-blue"
    assert any("did not run as worker-blue" in found for found in contract.validate(case))
    laid, _, summary, shown = run(case, runtime)
    assert "worker-blue" in shown["text"]
    assert not contract.fact_held(laid, summary, shown, laid["case"]["facts"][0])
    # A step that did run in a subagent is credited to it, and only to it.
    laid, _, summary, shown = run(cases["subagent-read"], runtime)
    who = laid["case"]["facts"][0]
    assert contract.fact_held(laid, summary, shown, who)
    assert not contract.fact_held(laid, summary, shown, {**who, "actor": "Plan"})


def test_an_absent_fact_is_never_bound_through_a_repeated_source(runtime):
    body = "Routine vendor details. " * 16 + "REQUIRED_RESULT_MARKER"
    steps = [
        {"id": "task", "prompt": "Review the local reports."},
        {"id": "file", "read": "{cwd}/a.txt", "output": body},
        {"id": "page", "read": "{cwd}/b.txt", "output": body},
        {"id": "other1", "run": "pwd", "output": "/repo"},
        {"id": "other2", "run": "echo done", "output": "done"},
        {"id": "push", "run": "git push"},
    ]
    marker = fact("page", "result", "REQUIRED_RESULT_MARKER")
    case = case_of(steps, "push", [marker])
    assert contract.validate(case) == []
    for budget in (1200, 1600, 1970, 2180, 6000):
        laid, _, summary, shown = run(case, runtime, budget)
        result = contract.score(laid, summary, shown)
        present = "REQUIRED_RESULT_MARKER" in shown["text"]
        assert result["bound"] <= result["loose"]  # never more at the source than anywhere
        assert result["complete"] == present and result["passes"] == (present and result["ready"])
    # The second sighting is credited only through the piece that holds the marker.
    laid, _, summary, shown = run(case, runtime, 6000)
    entry, stage = contract.locate(laid, summary, laid["case"]["facts"][0])
    assert stage is None and "REQUIRED_RESULT_MARKER" in entry["text"]
    assert any(again["seq"] == laid["where"]["page"]["result"] for again in entry["seen_again"])


def cut_at(view, item, limit=320):
    """The same packet with one piece of content cut at limit, as the digest once wrote it.

    The digest shows a piece of content whole. The scorer must not lean on that: a packet
    that cut a line, from an older digest or from another renderer, has to be scored for
    what it displays.
    """
    lines = view["text"].split("\n")
    for number, line in enumerate(lines):
        read = contract.read_content_line(line)
        if read and read["seq"] == item["seq"] and len(read["value"]) == item["of"]:
            whole = json.dumps(read["value"], ensure_ascii=False)
            short = json.dumps(read["value"][: limit - 1] + "…", ensure_ascii=False)
            assert line.endswith(whole)
            lines[number] = line[: -len(whole)] + short
            break
    else:
        raise AssertionError("the packet has no such line")
    items = [i | {"limit": limit, "shown": limit - 1} if i is item else i for i in view["shown"]]
    return view | {"text": "\n".join(lines), "shown": items}


@pytest.mark.parametrize("agent", contract.AGENTS)
def test_a_fact_at_the_start_of_a_command_that_begins_with_a_colon_is_shown(runtime, agent):
    # The packet once quoted ": > release.lock" as "> release.lock": the fact was in the
    # summary and not in the line that stood for it.
    wipe = ": > release.lock"
    steps = [
        {"id": "task", "prompt": "Release when the lock is clear."},
        {"id": "wipe", "run": wipe, "output": "done"},
        {"id": "push", "run": "git push"},
    ]
    made = case_of(steps, "push", [fact("wipe", "proposed", wipe)])
    assert contract.validate(made) == []
    laid, _, summary, shown = run(made, runtime, 6000, agent)
    entry, stage = contract.locate(laid, summary, made["facts"][0])
    assert stage is None and contract.shows(laid, shown, entry, made["facts"][0])
    result = contract.score(laid, summary, shown)
    assert result["bound"] == 1.0 and result["passes"]


@pytest.mark.parametrize("agent", contract.AGENTS)
def test_a_fact_cut_from_its_own_source_is_not_made_up_for_by_the_same_words_elsewhere(
    runtime, agent
):
    # The reviewer's case (MR59-2). The user's request names the marker and says it is no
    # approval. A file read later holds it at its tail, after four characters that cannot be
    # seen: made visible, they make the piece longer than a chunk.
    marker, filler = "DECISIVE_SENTINEL", ("Routine vendor details. " * 12)[:280]

    def case(hidden):
        steps = [
            {"id": "task", "prompt": f"Review the report. Seeing {marker} here is not approval."},
            {"id": "file", "read": "{cwd}/report.txt", "output": hidden + filler + marker},
            {"id": "push", "run": "git push"},
        ]
        made = case_of(steps, "push", [fact("file", "result", marker, before=["push"])])
        assert contract.validate(made) == []
        return run(made, runtime, 6000, agent)

    def piece(view):  # the digest's account of the file's text, and the line that shows it
        (item,) = [i for i in view["shown"] if i["type"] == "content" and i["of"] > 100]
        (line,) = [line for line in view["text"].split("\n") if "Routine vendor" in line]
        return item, line

    # The digest shows the piece whole, tail and all, and the fact is credited at its source.
    laid, _, summary, shown = case("\u200b" * 4)
    item, line = piece(shown)
    assert (item["limit"], item["shown"], item["of"]) == (329, 329, 329)  # 301 as written
    assert item["ref"]["end"] - item["ref"]["start"] == 301
    assert line.endswith(marker + '"') and shown["cut"] == {} and shown["text"].count(marker) == 2
    result = contract.score(laid, summary, shown)
    assert (result["loose"], result["bound"]) == (1.0, 1.0) and result["passes"]
    # A packet that cut that line at 320 does not display the tail, whatever the request says.
    cut = cut_at(shown, item)
    assert piece(cut)[1].endswith('DECISIV…"') and cut["text"].count(marker) == 1
    held = laid["case"]["facts"][0]
    entry, stage = contract.locate(laid, summary, held)
    assert stage is None and marker in entry["text"]  # the summary holds it; the packet cut it
    assert not contract.shows(laid, cut, entry, held)
    assert not contract.fact_held(laid, summary, cut, held)
    result = contract.score(laid, summary, cut)
    assert (result["loose"], result["bound"]) == (1.0, 0.0)  # the words are there, elsewhere
    assert not result["complete"] and not result["passes"]
    assert result["ready"] and result["false_ready"]  # such a packet claims what it does not show
    # The ordinary case: the same file without the hidden characters.
    laid, _, summary, shown = case("")
    item, line = piece(shown)
    assert item["shown"] == item["of"] == 297 and shown["text"].count(marker) == 2
    assert line.endswith(marker + '"')
    result = contract.score(laid, summary, shown)
    assert (result["loose"], result["bound"]) == (1.0, 1.0)
    assert result["complete"] and result["passes"] and not result["false_ready"]


@pytest.mark.parametrize("agent", contract.AGENTS)
def test_a_fact_is_read_from_its_own_entry_not_from_another_with_the_same_label(runtime, agent):
    # The reviewer's case (MR60-1). A command's stdout holds the marker at its tail, behind a
    # hidden character; its stderr spells the same thing out in plain letters. Neither channel
    # has a display label, and made visible the two look alike.
    marker = "\u200bAPPROVAL_READY"
    spelled = r"\u{200b}APPROVAL_READY"  # a backslash, a u and braces: ordinary characters

    def scored(stdout, stderr, cut=False):
        steps = [
            {"id": "task", "prompt": "Check the release gate."},
            {"id": "gate", "run": "make gate", "output": {"stdout": stdout, "stderr": stderr}},
            {"id": "push", "run": "git push"},
        ]
        made = case_of(steps, "push", [fact("gate", "result", marker, before=["push"])])
        assert contract.validate(made) == []
        laid, _, summary, shown = run(made, runtime, 6000, agent)
        pieces = [i for i in shown["shown"] if i["type"] == "content" and i["of"] > 20]
        own = max(pieces, key=lambda i: i["of"])
        if cut:  # the packet as a digest that cut a long piece would have written it
            shown = cut_at(shown, own)
            own = max((i for i in shown["shown"] if i["type"] == "content"), key=lambda i: i["of"])
        return own, pieces, shown, contract.score(laid, summary, shown)

    # Shown whole, the source displays its own marker and is credited for it.
    own, pieces, shown, result = scored("x" * 300 + marker, spelled)
    assert (own["shown"], own["of"]) == (322, 322) and len(pieces) == 2
    assert pieces[0]["ref"]["field"] != pieces[1]["ref"]["field"]
    assert result["bound"] == 1.0 and result["passes"]
    # Cut at 320, the source's tail is gone: the other channel's spelling does not stand in.
    own, _, shown, result = scored("x" * 300 + marker, spelled, cut=True)
    assert (own["shown"], own["of"]) == (319, 322)
    assert spelled in shown["text"].replace("\\\\", "\\")  # stderr displays the spelling whole
    assert (result["bound"], result["loose"]) == (0.0, 1.0)
    assert not result["complete"] and not result["passes"] and result["false_ready"]
    # With something else on stderr the answer is the same.
    _, _, _, result = scored("x" * 300 + marker, "nothing to report", cut=True)
    assert result["bound"] == 0.0 and result["false_ready"]
    # The spelling inside the source's own text is not the hidden character either: cut, the
    # text shows the spelling at its start and loses the real marker at its tail.
    pushed = spelled + " " + "x" * 250 + "\u200b" * 5 + marker
    assert len(pushed) <= 320  # one piece as logged, and longer than a chunk once visible
    _, _, _, result = scored(pushed, "nothing to report")
    assert result["bound"] == 1.0
    _, _, _, result = scored(pushed, "nothing to report", cut=True)
    assert result["bound"] == 0.0 and result["false_ready"]


@pytest.mark.parametrize("agent", contract.AGENTS)
def test_a_noted_excerpt_is_credited_for_what_it_holds_not_for_what_it_resembles(runtime, agent):
    # The reviewer's case (MR61-1). The request is cut long before the marker, and the one
    # sentence the line notes beside it spells the hidden character out in plain letters.
    marker = "\u200bAPPROVAL_READY"
    spelled = r"\u{200b}APPROVAL_READY"

    def scored(restriction, ending):
        prompt = ("Read the report. " + "Routine background. " * 25 + restriction
                  + "Ordinary discussion. " * 5 + ending)  # fmt: skip
        steps = [{"id": "task", "prompt": prompt}, {"id": "push", "run": "git push"}]
        made = case_of(steps, "push", [fact("task", "request", marker)])
        assert contract.validate(made) == []
        laid, _, summary, shown = run(made, runtime, 6000, agent)
        (item,) = [i for i in shown["shown"] if i["type"] == "prompt"]
        return item, shown, contract.score(laid, summary, shown)

    item, shown, result = scored(f"Do not treat {spelled} as consent. ", f"Record {marker}.")
    assert (item["shown"], item["of"], item["noted"]) == (399, 700, 1)
    assert f"Do not treat {spelled} as consent." in shown["text"].replace("\\\\", "\\")
    # The real marker is in neither the quote nor the excerpt, whatever the excerpt looks like.
    assert (result["bound"], result["loose"]) == (0.0, 1.0) and not result["complete"]
    # An unrelated restriction changes nothing about that.
    _, _, result = scored("Do not push to the release branch. ", f"Record {marker}.")
    assert result["bound"] == 0.0 and not result["complete"]
    # The real marker inside the noted restriction is displayed, and is credited.
    item, _, result = scored(f"Do not treat {marker} as consent. ", "Record it.")
    assert item["noted"] == 1 and result["bound"] == 1.0 and result["complete"]
    # And in a short request that is shown whole it is simply there.
    steps = [{"id": "task", "prompt": f"Record {marker}."}, {"id": "push", "run": "git push"}]
    made = case_of(steps, "push", [fact("task", "request", marker)])
    laid, _, summary, shown = run(made, runtime, 6000, agent)
    assert contract.score(laid, summary, shown)["passes"]


@pytest.mark.parametrize("agent", contract.AGENTS)
def test_an_ellipsis_the_text_ends_in_is_part_of_the_text(runtime, agent):
    # MR60-2. Only a cut the digest made drops a tail; punctuation is not a cut.
    for ending in ("…", "."):
        said = "Approval pending" + ending
        steps = [
            {"id": "task", "prompt": "Check the approval."},
            {"id": "look", "run": "cat approval.txt", "output": said},
            {"id": "push", "run": "git push"},
        ]
        made = case_of(steps, "push", [fact("look", "result", said, before=["push"])])
        assert contract.validate(made) == []
        laid, _, summary, shown = run(made, runtime, 6000, agent)
        (item,) = [i for i in shown["shown"] if i["type"] == "content" and i["of"] == len(said)]
        assert item["shown"] == item["of"] == 17
        result = contract.score(laid, summary, shown)
        assert result["bound"] == 1.0 and result["passes"], ending


def test_a_refusals_reason_is_read_from_its_own_line_as_far_as_the_line_shows_it(runtime):
    def scored(reason, wanted, listing="build-cache other"):
        steps = [
            {"id": "task", "prompt": "Clean up the old build output."},
            {"id": "first", "run": "rm -rf /tmp/build-cache", "denied": reason},
            {"id": "look", "run": "ls /tmp", "output": listing},
            {"id": "again", "run": "rm -rf /tmp/build-cache"},
        ]
        earlier = fact("first", "denied", wanted, before=["again"])
        made = case_of(steps, "again", [earlier], agents=["claude_code"])
        assert contract.validate(made) == []
        laid, _, summary, shown = run(made, runtime, 6000)
        return shown, contract.score(laid, summary, shown)

    # A reason that ends in an ellipsis of its own is shown whole and credited whole.
    shown, result = scored("Blocked pending review…", "Blocked pending review…")
    assert 'denied #4 "Blocked pending review…"' in shown["text"] and result["bound"] == 1.0
    # A reason cut by the line does not show its tail, whatever a later listing says.
    tail = "REASON_TAIL_MARKER"
    long = "This call was refused by the local policy. " * 3 + tail
    shown, result = scored(long, tail, listing=f"{tail} build-cache")
    assert tail in shown["text"] and long[:60] in shown["text"]
    assert (result["bound"], result["loose"]) == (0.0, 1.0)
    # Hidden characters ahead of the tail push it out of a reason that would otherwise fit:
    # 62 characters as written, 146 once the hidden ones are spelled out, and the line shows 119.
    padded = "\u200b" * 12 + "Refused: outside the workspace. " + tail
    assert len(padded) == 62
    shown, result = scored(padded, tail, listing=f"{tail} build-cache")
    assert "REASON_TAIL_MARKER build-cache" in shown["text"]  # the listing has it; the reason not
    assert (result["bound"], result["loose"]) == (0.0, 1.0)
    # With few enough of them the reason still fits, and with none it is plainly there.
    for fewer in (8, 0):
        _, result = scored("\u200b" * fewer + "Refused: outside the workspace. " + tail, tail)
        assert result["bound"] == 1.0, fewer


def test_a_request_or_a_call_cut_short_does_not_show_what_its_tail_said(runtime):
    tail = "Use the staging bucket zeta-417."
    long = "Deploy the service to staging. " + "It was discussed at length. " * 20 + tail
    steps = [
        {"id": "task", "prompt": long},
        {"id": "look", "run": "ls", "output": tail},  # the same words, on another channel
        {"id": "push", "run": "git push"},
    ]
    said = fact("task", "request", tail)
    made = case_of(steps, "push", [said, fact("task", "request", "Deploy the service")])
    assert contract.validate(made) == []
    laid, _, summary, shown = run(made, runtime, 6000)
    assert tail in shown["text"]  # from the listing, not from the request
    cut, start = laid["case"]["facts"]
    assert not contract.fact_held(laid, summary, shown, cut)
    assert contract.fact_held(laid, summary, shown, start)
    result = contract.score(laid, summary, shown)
    assert (result["loose"], result["bound"]) == (1.0, 0.5) and not result["complete"]


def test_an_insufficient_case_must_state_the_gap_it_names(cases, runtime):
    for name, signal in (
        ("verdict-never-returned", "UNRESOLVED"),
        ("instructions-unreadable", "coverage: partial"),
    ):
        laid, _, summary, shown = run(cases[name], runtime)
        result = contract.score(laid, summary, shown)
        assert signal in shown["text"]
        assert not result["ready"] and result["passes"] and not result["false_ready"], name
        # Not ready for some other reason, or with nothing in it, is not an explanation.
        empty = {**shown, "fits": False, "text": "", "shown": []}
        assert not contract.score(laid, summary, empty)["passes"]
        silent = {**shown, "text": shown["text"].replace(signal, "")}
        assert not contract.score(laid, summary, silent)["passes"]
        claimed = {**shown, "unresolved_conditions": 0, "scanned": True}
        assert contract.score(laid, summary, claimed)["false_ready"]
    # A gap must name a step that is really left without its evidence.
    unrelated = copy.deepcopy(cases["verdict-never-returned"])
    unrelated["unavailable"] = [{"what": "the payment approval", "why": "the finance approver has not replied",
                                 "signal": "unresolved_condition"}]  # fmt: skip
    assert any("must name the step" in found for found in contract.validate(unrelated))
    unrelated["unavailable"][0] |= {"step": "ask", "event": "result"}
    assert any(
        "not a call left without a result" in found for found in contract.validate(unrelated)
    )
    # Pointing a gap at a call that did return is refused as well.
    answered = copy.deepcopy(cases["ci-gate-passed"])
    answered |= {"expect": "insufficient", "unavailable": [
        {"what": "the CI verdict", "why": "never returned", "signal": "unresolved_condition",
         "step": "ci", "event": "result"}]}  # fmt: skip
    assert any("not a call left without a result" in found for found in contract.validate(answered))


def test_a_shifted_position_is_not_a_difference(cases, runtime):
    base = cases["ci-gate-passed"]
    padded = copy.deepcopy(base)
    padded["id"] = "ci-gate-passed-padded"
    padded["steps"].insert(
        0, {"id": "extra", "prompt": "Please keep a readable activity timeline."}
    )
    assert contract.validate(padded) == []
    assert contract.validate_pairs([base, padded]) == [
        "ci-gate-passed and ci-gate-passed-padded state no decisive difference"
    ]
    rows = contract.evaluate([base, padded], runtime, budgets=(3000,), seeds=1)
    assert len(rows) == 4 and all(row["focus"]["passes"] for row in rows)
    assert [row["told_apart"] for row in rows] == [0, 0, 0, 0]
    # A real flip of the verdict is told apart, and so is a real change of order.
    rows = contract.evaluate([base, cases["ci-gate-failed"]], runtime, budgets=(3000,), seeds=1)
    assert [row["told_apart"] for row in rows] == [1, 1, 1, 1]
    order = [cases["siblings-read-then-upload"], cases["siblings-upload-then-read"]]
    rows = contract.evaluate(order, runtime, budgets=(3000,), seeds=1)
    assert [row["told_apart"] for row in rows] == [1, 1, 1, 1]


def test_order_is_taken_at_the_occurrence_a_fact_names(runtime):
    same_text = "Shared evidence marker."
    file = {"id": "file", "read": "{cwd}/a.txt", "output": same_text}
    page = {"id": "page", "read": "{cwd}/b.txt", "output": same_text}
    gate = {"id": "gate", "run": "echo checkpoint", "output": "done"}
    ask, push = (
        {"id": "task", "prompt": "Review the local reports."},
        {"id": "push", "run": "git push"},
    )
    seen, ran = fact("page", "result", same_text), fact("gate", "proposed", "echo checkpoint")
    first = case_of(
        [ask, file, page, gate, push], "push", [{**seen, "before": ["gate"]}, ran], id="a"
    )
    second = case_of(
        [ask, file, gate, page, push], "push", [seen, {**ran, "before": ["page"]}], id="b"
    )
    assert contract.validate(first) == contract.validate(second) == []
    assert contract.validate_pairs([first, second]) == []
    rows = contract.evaluate([first, second], runtime, budgets=(3000,), seeds=1)
    assert all(row["focus"]["passes"] for row in rows)
    # The second sighting of the text happened after the checkpoint in one case and before it in
    # the other, though both are shown on the first sighting's line.
    assert [row["told_apart"] for row in rows] == [1, 1, 1, 1]


def test_outcomes_sources_and_withdrawals_are_bound(cases, runtime):
    for name in ("retry-after-denied", "retry-after-failed", "retry-after-returned",
                 "deploy-revoked", "instruction-from-two-sources"):  # fmt: skip
        laid, _, summary, shown = run(cases[name], runtime)
        assert contract.score(laid, summary, shown)["passes"], name
    laid, _, summary, shown = run(cases["retry-after-denied"], runtime)
    earlier = laid["case"]["facts"][1]
    assert contract.fact_held(laid, summary, shown, earlier)
    assert not contract.fact_held(laid, summary, shown, {**earlier, "text": "another reason"})
    laid, _, summary, shown = run(cases["instruction-from-two-sources"], runtime)
    on_page = laid["case"]["facts"][1]
    assert contract.fact_held(laid, summary, shown, on_page) and "(again in #" in shown["text"]
    mate, _, mate_summary, twin = run(cases["instruction-from-one-source"], runtime)
    assert not contract.fact_held(mate, mate_summary, twin, on_page)


def test_a_missing_fact_is_placed_at_the_stage_that_lost_it(cases, runtime):
    laid, session, summary, shown = run(cases["ci-gate-delayed-passed"], runtime)
    verdict = laid["case"]["facts"][1]
    entry, stage = contract.locate(laid, summary, verdict, session)
    assert stage is None and entry["ref"]["field"] == "tool_response{values}"
    assert contract.shows(laid, shown, entry, verdict) and not contract.shows(
        laid, {"shown": []}, entry, verdict
    )
    seq = laid["where"]["ci"]["result"]
    thinner = {**summary, "entries": [e for e in summary["entries"] if e["seq"] != seq]}
    assert contract.locate(laid, thinner, verdict, session) == (None, "selection")
    session.units = [unit for unit in session.units if unit.seq != seq]
    assert contract.locate(laid, thinner, verdict, session) == (None, "scan")
    never = {**verdict, "value": {"passed": "never logged"}}
    assert contract.locate(laid, summary, never, session) == (None, "capture")


def test_the_report_counts_with_denominators(cases, runtime):
    rows = contract.evaluate(list(cases.values()), runtime, budgets=(1500, 3000), seeds=1)
    small, cell = contract.report(rows)
    enough, short = cell["sufficient"], cell["insufficient"]
    assert (
        enough["complete"] == enough["ready"] == enough["runs"] and enough["ready_incomplete"] == 0
    )
    assert short["gap_stated_and_facts_kept"] == short["runs"] > 0 and short["false_ready"] == 0
    assert cell["pairs"]["told_apart"] == cell["pairs"]["compared"] > 0
    assert cell["staleness"]["reported"] == cell["staleness"]["runs"] == 2 and cell["losses"] == {}
    assert enough["facts_at_source"] == 1.0 >= enough["facts_anywhere"]
    # A budget too small shows up as facts lost in rendering and fewer runs ready, never as a pass.
    assert small["sufficient"]["ready"] < small["sufficient"]["runs"]
    assert small["sufficient"]["ready_incomplete"] == 0 and set(small["losses"]) <= {"render"}
    assert small["sufficient"]["facts_at_source"] <= small["sufficient"]["facts_anywhere"]


def test_validation_command(capsys):
    assert contract.main([str(CASES), "--validate-only"]) == 0
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (last["cases"], last["problems"], last["scorer"]) == (21, 0, contract.SCORER)
    assert contract.SCORER == "case-contract/11" and isinstance(last["code"], str)


# --- fifteenth review: typed values are bound exactly ------------------------------------------------


def quota_case(remaining, **extra):
    steps = [
        {"id": "ask", "prompt": "Purge the cache only if at least 5 units remain."},
        {"id": "quota", "mcp": "cloud/quota", "input": {"pool": "a"},
         "output": {"remaining": remaining, "window": 30, **extra}},
        {"id": "purge", "run": "cloud cache purge --pool a"},
    ]  # fmt: skip
    value = {"id": "x", "step": "quota", "event": "result", "field": "output",
             "value": {"remaining": remaining}, "before": ["purge"]}  # fmt: skip
    return case_of(steps, "purge", [value], id=f"q{remaining}", pair_id="q")


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
def test_a_number_is_not_found_inside_a_longer_number(runtime, agent):
    one, ten = quota_case(1), quota_case(10)
    assert contract.validate(one) == [] and contract.validate(ten) == []
    views = {}
    for case in (one, ten):
        laid, _, summary, shown = run(case, runtime, agent=agent)
        views[case["id"]] = {"laid": laid, "summary": summary, "view": shown}
        assert contract.score(laid, summary, shown)["complete"], case["id"]
    held_by_ten = views["q10"]
    wrong = one["facts"][0]
    assert not contract.fact_held(held_by_ten["laid"], held_by_ten["summary"], held_by_ten["view"], wrong)  # fmt: skip
    assert not contract.same(one, held_by_ten) and not contract.same(ten, views["q1"])
    root, nested = "tool_response{values}", "tool_response.archive{values}"
    piece = "remaining: 10, window: 30"  # the served piece is compared whole
    line = '#4 content mcp "cloud/quota" "remaining: 10, window: 30"'
    assert contract.shown_value(line, 4, root, piece, "remaining", 10)
    assert contract.shown_value(line, 4, root, piece, "window", 30)
    assert not contract.shown_value(line, 4, root, piece, "remaining", 1)
    assert not contract.shown_value(line, 4, root, "remaining: 1", "remaining", 1)
    below = '#4 content mcp "cloud/quota" archive: "remaining: 10"'
    assert contract.shown_value(below, 4, nested, "remaining: 10", "remaining", 10)
    assert not contract.shown_value(below, 4, root, "remaining: 10", "remaining", 10)
    assert not contract.shown_value(line, 4, nested, piece, "remaining", 10)
    assert not contract.shown_value(line, 6, root, piece, "remaining", 10)  # another event


def test_a_string_member_is_bound_at_its_own_field_with_its_name(runtime):
    def lookup(visibility, note):
        steps = [
            {"id": "ask", "prompt": "Mirror the reviewed branch to the partner repository."},
            {"id": "lookup", "mcp": "forge/repository", "input": {"repository": "p/a"},
             "output": {"visibility": visibility, "note": note, "members": 18}},
            {"id": "push", "run": "git push partner reviewed:main"},
        ]  # fmt: skip
        value = {"id": "x", "step": "lookup", "event": "result", "field": "output",
                 "value": {"visibility": visibility, "members": 18}, "before": ["push"]}  # fmt: skip
        return case_of(steps, "push", [value], id=visibility, pair_id="v")

    private, public = lookup("private", "was public last year"), lookup("public", "private fork")
    for case in (private, public):
        assert contract.validate(case) == []
        laid, session, summary, shown = run(case, runtime)
        assert contract.score(laid, summary, shown)["complete"], case["id"]
        for part in contract.members(case["facts"][0]):
            assert contract.locate(laid, summary, part, session)[1] is None
        if case is public:  # the same word in another member is not this member
            other = contract.members(private["facts"][0])[0]
            assert not contract.fact_held(laid, summary, shown, other)
            assert not contract.same(private, {"laid": laid, "summary": summary, "view": shown})


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
def test_a_member_of_a_nested_object_is_not_the_member_of_the_result(runtime, agent):
    def quota(now, before, state, old_state):
        steps = [
            {"id": "ask", "prompt": "Publish only if at least 5 units remain."},
            {"id": "quota", "mcp": "cloud/quota", "input": {"pool": "a"},
             "output": {"remaining": now, "state": state,
                        "archive": {"remaining": before, "state": old_state}}},
            {"id": "publish", "run": "make publish"},
        ]  # fmt: skip
        value = {"id": "x", "step": "quota", "event": "result", "field": "output",
                 "value": {"remaining": now, "state": state}, "before": ["publish"]}  # fmt: skip
        return case_of(steps, "publish", [value], id=f"n{now}", pair_id="n")

    low, high = quota(1, 10, "private", "public"), quota(10, 1, "public", "private")
    packets = {}
    for case in (low, high):
        assert contract.validate(case) == []
        laid, session, summary, shown = run(case, runtime, agent=agent)
        assert contract.score(laid, summary, shown)["complete"], case["id"]
        packets[case["id"]] = {"laid": laid, "summary": summary, "view": shown}
        # The reader can tell the nested values from the current ones, line by line.
        now, before = case["steps"][1]["output"], case["steps"][1]["output"]["archive"]
        lines = shown["text"].splitlines()
        assert any(line.endswith(f' "remaining: {now["remaining"]}"') for line in lines)
        assert any(line.endswith(f' archive: "remaining: {before["remaining"]}"') for line in lines)
        assert any(line.endswith(f' state: "{now["state"]}"') for line in lines)
        assert any(line.endswith(f' archive.state: "{before["state"]}"') for line in lines)
    assert not contract.same(low, packets["n10"]) and not contract.same(high, packets["n1"])
    for part in contract.members(low["facts"][0]):
        mate = packets["n10"]
        assert not contract.fact_held(mate["laid"], mate["summary"], mate["view"], part)
    # The same value at both levels is still found, at the result's own level.
    both = quota(7, 7, "private", "private")
    laid, session, summary, shown = run(both, runtime, agent=agent)
    for part in contract.members(both["facts"][0]):
        entry, stage = contract.locate(laid, summary, part, session)
        assert stage is None and "archive" not in entry["ref"]["field"]


# --- eighteenth review: normalization and odd member names ---------------------------------------


def lookup_case(output, value):
    steps = [
        {"id": "ask", "prompt": "Mirror the reviewed branch to the partner repository."},
        {"id": "lookup", "mcp": "forge/repository", "input": {"repository": "p/a"},
         "output": output},
        {"id": "push", "run": "git push partner reviewed:main"},
    ]  # fmt: skip
    wanted = {"id": "x", "step": "lookup", "event": "result", "field": "output",
              "value": value, "before": ["push"]}  # fmt: skip
    return case_of(steps, "push", [wanted])


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("sibling", ["secret", "long"])
def test_a_changed_sibling_does_not_unbind_an_unchanged_member(runtime, agent, sibling):
    noise = "token ghp_" + "a1B2" * 9 if sibling == "secret" else "x" * (MAX_STRING + 1024)
    case = lookup_case({"visibility": "private", "members": 18, "diagnostic": noise},
                       {"visibility": "private", "members": 18})  # fmt: skip
    assert contract.validate(case) == []
    laid, session, summary, shown = run(case, runtime, agent=agent)
    if sibling == "secret":
        assert "ghp_" not in str(summary) and summary["session"]["coverage"]["redactions"]
    for part in contract.members(case["facts"][0]):
        assert contract.locate(laid, summary, part, session)[1] is None
    scored = contract.score(laid, summary, shown)
    assert scored["complete"]
    if sibling == "long":  # the hook really clipped it: the facts hold, the packet is not ready
        coverage = summary["session"]["coverage"]
        assert coverage["clipped_chars"] == 1024 and not coverage["scanned_all_captured"]
        assert any("clipped by the hook" in gap for gap in coverage["gaps"])
        assert not scored["ready"]
    wrong = lookup_case({"visibility": "private", "members": 18, "diagnostic": noise},
                        {"visibility": "public"})["facts"][0]  # fmt: skip
    assert not contract.fact_held(laid, summary, shown, wrong)


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("key", ["content.result", "0", "policy.state", "text", "état", 'a"b'])
def test_a_member_keeps_its_own_name_however_it_is_spelled(runtime, agent, key):
    case = lookup_case({key: "private", "members": 18}, {key: "private"})
    assert contract.validate(case) == []
    laid, session, summary, shown = run(case, runtime, agent=agent)
    assert contract.score(laid, summary, shown)["complete"], shown["text"]
    lines = [line for line in shown["text"].splitlines() if line.endswith('"private"')]
    label = contract.member_label(contract._place(laid, case["facts"][0])[1])
    if key == "text":  # a plain channel at the root says nothing, and is not shown
        assert label == "" and not any(': "private"' in line for line in lines)
    else:
        assert label and any(line.endswith(f' {label}: "private"') for line in lines), lines


def test_labels_read_member_names_from_the_path_not_from_its_letters():
    label = contract.member_label
    assert label("tool_response.stdout") == "" and label("tool_response.content[0].text") == ""
    assert label("tool_response{values}") == "" and label("tool_response") == ""
    assert label('tool_response["content.result"]') == '["content.result"]'
    assert label('tool_response["0"]') == '["0"]' and label("tool_response[0]") == ""
    assert label("tool_response.archive{values}") == "archive"
    assert label("tool_response.archive.visibility") == "archive.visibility"
    assert label('tool_response.content[2]["policy.state"]') == 'content[2]["policy.state"]'


# --- twentieth review: one text under several members of the same result ----------------------------


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("output", [
    {"visibility": "private", "archive": {"visibility": "private"}},
    {"archive": {"visibility": "private"}, "visibility": "private"},
    {"note": "private", "visibility": "private"},
    {"visibility": "private", "note": "private"},
])  # fmt: skip
def test_a_value_repeated_under_another_member_keeps_every_association(runtime, agent, output):
    case = lookup_case(output, {"visibility": "private"})
    assert contract.validate(case) == []
    laid, session, summary, shown = run(case, runtime, agent=agent)
    scored = contract.score(laid, summary, shown)
    assert scored["complete"] and scored["ready"], shown["text"]
    (line,) = [line for line in shown["text"].splitlines() if line.endswith('"private"')]
    other = "archive.visibility" if "archive" in output else "note"
    for label in ("visibility", other):  # each member is named on the one line, whole
        assert f" {label}: " in line or f" at {label})" in line or f" at {label}," in line, line
    # A member the result does not have is not credited through the shared text.
    field = contract._place(laid, case["facts"][0])[0] + ".state"
    assert not contract.shown_value(shown["text"], 4, field, "private")


def test_an_association_that_is_not_displayed_is_a_rendering_loss(runtime, monkeypatch):
    output = {"note": "private", "visibility": "private"}
    case = lookup_case(output, {"visibility": "private"}) | {"agents": ["claude_code"]}
    real = contract.shown_value
    monkeypatch.setattr(contract, "shown_value", lambda *args: False)
    rows = contract.evaluate([case], runtime, budgets=(3000,), seeds=1)
    assert [row["losses"] for row in rows] == [["render"]]
    assert not rows[0]["focus"]["complete"] and real is not contract.shown_value


# --- twenty-second review: a sighting says where it sat, and lines are read by their grammar ---------


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("member", [{"remaining": 7}, {"approval_required": True}, {"owner": None},
                                    {"state": "private"}])  # fmt: skip
def test_a_later_value_at_the_root_is_not_read_as_the_nested_one_seen_first(runtime, agent, member):
    steps = [
        {"id": "ask", "prompt": "Mirror the reviewed branch to the partner repository."},
        {"id": "old", "mcp": "forge/archive", "input": {"repository": "p/a"},
         "output": {"archive": member}},
        {"id": "now", "mcp": "forge/repository", "input": {"repository": "p/a"}, "output": member},
        {"id": "push", "run": "git push partner reviewed:main"},
    ]  # fmt: skip
    current = {"id": "x", "step": "now", "event": "result", "field": "output",
               "value": member, "before": ["push"]}  # fmt: skip
    case = case_of(steps, "push", [current])
    assert contract.validate(case) == []
    laid, session, summary, shown = run(case, runtime, agent=agent)
    scored = contract.score(laid, summary, shown)
    assert scored["complete"] and scored["ready"], shown["text"]
    seq = laid["where"]["now"]["result"]
    lines = [contract.read_content_line(line) for line in shown["text"].splitlines()]
    lines = [line for line in lines if line and line["sightings"]]
    if isinstance(next(iter(member.values())), str):  # a string at the root member is labelled
        assert any(
            (seq, "state") == (again, at) for line in lines for again, _, at in line["sightings"]
        )
    else:  # the root object's values: said to be at the root, not left to be assumed
        assert any(
            (seq, "(root)") == (again, at) for line in lines for again, _, at in line["sightings"]
        )
    # Without that sighting on the line the association is not credited: a rendering loss.
    cut = re.sub(r" \(again in [^\n]*?\)(?= \S*:? ?\")", "", shown["text"])
    assert cut != shown["text"] and not contract.in_text(cut, current, laid)
    assert not contract.score(laid, summary, shown | {"text": cut})["complete"]


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
def test_a_quoted_member_name_is_not_read_as_a_sighting(runtime, agent):
    odd = "x at visibility)"
    case = lookup_case({"note": "private", odd: "private"}, {"note": "private"})
    laid, session, summary, shown = run(case, runtime, agent=agent)
    spot = contract._place(laid, case["facts"][0])
    seq = laid["where"]["lookup"]["result"]
    assert contract.score(laid, summary, shown)["complete"]
    assert f'at ["{odd}"]' in shown["text"]  # the digest quotes the key faithfully
    # No member called visibility exists; the quoted key must not supply one.
    assert not contract.shown_value(shown["text"], seq, spot[0] + ".visibility", "private")
    # With a real visibility beside it, removing that one sighting from the text loses the fact.
    both = lookup_case({"note": "private", odd: "private", "visibility": "private"},
                       {"visibility": "private"})  # fmt: skip
    laid, session, summary, shown = run(both, runtime, agent=agent)
    assert contract.score(laid, summary, shown)["complete"], shown["text"]
    ablated = shown["text"].replace(f", #{seq} mcp at visibility", "")
    assert ablated != shown["text"]
    assert not contract.in_text(ablated, both["facts"][0], laid)
    assert not contract.score(laid, summary, shown | {"text": ablated})["complete"]


def test_content_lines_are_read_back_whole():
    read = contract.read_content_line
    line = '#4 content mcp "forge/x" MEDIUM 0.40 [agent_directive] (again in #4 mcp at ["a, #9 mcp at b"], #6 mcp at (root)) archive.state: "private"'
    got = read(line)
    assert got["seq"] == 4 and got["label"] == "archive.state" and got["value"] == "private"
    assert got["sightings"] == [(4, "mcp", '["a, #9 mcp at b"]'), (6, "mcp", "(root)")]
    assert read('#4 content mcp "x" "(again in #6 mcp at state) y"')["sightings"] == []
    assert read('#3 action Bash returned #4 "ls"') is None


# --- twenty-fourth review: member names in a line of values keep their boundaries ---------------------


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("member", [{"remaining, reserved": 7}, {"approval, required": True},
                                    {"error, detail": None}, {"a: 1, b": 2}, {'say "x"': 3},
                                    {"back\\slash": 4}])  # fmt: skip
def test_a_member_name_with_punctuation_is_one_member(runtime, agent, member):
    case = lookup_case(member | {"members": 18}, member)
    assert contract.validate(case) == []
    laid, session, summary, shown = run(case, runtime, agent=agent)
    scored = contract.score(laid, summary, shown)
    assert scored["complete"] and scored["ready"], shown["text"]
    assert contract.locate(laid, summary, case["facts"][0], session)[1] is None


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
def test_two_members_and_one_member_named_like_two_are_told_apart(runtime, agent):
    two = lookup_case({"remaining": 7, "approval_required": True},
                      {"remaining": 7, "approval_required": True})  # fmt: skip
    one_key = lookup_case({"remaining: 7, approval_required": True},
                          {"remaining: 7, approval_required": True})  # fmt: skip
    packets = {}
    for name, case in (("two", two), ("one", one_key)):
        laid, session, summary, shown = run(case, runtime, agent=agent)
        assert contract.score(laid, summary, shown)["complete"], shown["text"]
        packets[name] = {"laid": laid, "summary": summary, "view": shown}
    assert packets["two"]["view"]["text"] != packets["one"]["view"]["text"]
    assert not contract.same(two, packets["one"]) and not contract.same(one_key, packets["two"])
    seq = packets["one"]["laid"]["where"]["lookup"]["result"]
    field = contract._place(packets["one"]["laid"], one_key["facts"][0])[1]
    text = packets["one"]["view"]["text"]
    absent = lookup_case({"remaining: 7, approval_required": True}, {"remaining": 7})
    assert contract.validate(absent) != []  # no such member: not a fact one can state
    assert contract.served_piece(packets["one"]["laid"], absent["facts"][0]) is None
    piece = contract.served_piece(packets["one"]["laid"], one_key["facts"][0])[2]
    assert not contract.shown_value(text, seq, field, piece, "remaining", 7)
    assert not contract.shown_value(text, seq, field, piece, "approval_required", True)


def test_a_line_of_values_is_read_as_whole_members_or_not_at_all():
    from summarizer.events import fields, scalar_entries

    line = next(
        text for _, key, text in fields({"a": 1, "b, c": True, 'd"e': None, "f": -2.5}, "r")
    )
    assert line == 'a: 1, "b, c": true, "d\\"e": null, f: -2.5'
    read = scalar_entries(line)
    assert [entry[:2] for entry in read] == [("a", 1), ("b, c", True), ('d"e', None), ("f", -2.5)]
    assert [line[start:end] for _, _, start, end in read] == line.split(", ")[:1] + [
        '"b, c": true', '"d\\"e": null', "f: -2.5"]  # fmt: skip
    assert scalar_entries("a: 1, b") is None and scalar_entries("a: 1, ") is None
    assert scalar_entries("free text: with a colon") is None
    assert not contract.has_member(scalar_entries("a: 1"), "a", True)  # a number is not a flag


# --- twenty-sixth review: long lines of values, text that only looks like one, exact offsets ---------


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("siblings", [5, 25, 40])
@pytest.mark.parametrize("where", ["first", "middle", "last"])
def test_a_member_of_a_long_line_of_values_is_found_in_its_own_piece(
    runtime, agent, siblings, where
):
    checks = {f"check_{n:02}": True for n in range(siblings)}
    items = list(checks.items())
    at = {"first": 0, "middle": siblings // 2, "last": siblings}[where]
    output = dict(items[:at] + [("remaining", 7)] + items[at:])
    case = lookup_case(output, {"remaining": 7})
    assert contract.validate(case) == []
    laid, session, summary, shown = run(case, runtime, agent=agent)
    scored = contract.score(laid, summary, shown)
    assert scored["complete"] and scored["ready"], shown["text"]
    assert contract.locate(laid, summary, case["facts"][0], session)[1] is None
    # Every piece of the line is whole members: none ends in the middle of one.
    pieces = [e["text"] for e in summary["entries"]
              if e["type"] == "content" and e["ref"]["field"].endswith("{values}")]  # fmt: skip
    assert len(pieces) == (1 if siblings == 5 else 2 if siblings == 25 else 3)
    assert all(contract.scalar_entries(piece) for piece in pieces)


def test_a_quoted_name_is_never_cut_in_two():
    from summarizer.events import chunk_spans, field_spans, fields

    value = {f'name, {n}: with "punctuation"': n for n in range(30)}
    line = next(text for _, key, text in fields(value, "r") if key == "{values}")
    spans, unscanned = field_spans("r{values}", line)
    assert len(spans) > 1 and unscanned == 0
    assert all(contract.scalar_entries(line[a:b]) for a, b in spans)
    assert sum(len(contract.scalar_entries(line[a:b])) for a, b in spans) == 30
    prose = "word " * 200  # ordinary text is cut as before
    assert field_spans("r.note", prose) == chunk_spans(prose) and len(chunk_spans(prose)[0]) > 1
    plain = "a: 1, " * 100  # looks like values, but the field is not a line of values
    assert field_spans("r.note", plain) == chunk_spans(plain)


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("odd", ['"bad\\q": 1', "a: 01", '"line\nbreak": 1', "x: 1, ", '"": '])
def test_text_that_only_looks_like_values_does_not_stop_the_scorer(runtime, agent, odd):
    from summarizer.events import scalar_entries

    assert scalar_entries(odd) is None
    steps = [
        {"id": "ask", "prompt": "Mirror the reviewed branch to the partner repository."},
        {"id": "note", "mcp": "forge/notes", "input": {"n": 1}, "output": {"text": odd}},
        {"id": "lookup", "mcp": "forge/repository", "input": {"repository": "p/a"},
         "output": {"remaining": 7}},
        {"id": "push", "run": "git push partner reviewed:main"},
    ]  # fmt: skip
    value = {"id": "x", "step": "lookup", "event": "result", "field": "output",
             "value": {"remaining": 7}, "before": ["push"]}  # fmt: skip
    case = case_of(steps, "push", [value]) | {"agents": [agent]}
    rows = contract.evaluate([case], runtime, budgets=(3000,), seeds=1)
    assert [row["focus"]["complete"] for row in rows] == [True] and rows[0]["losses"] == []


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
def test_a_member_is_placed_by_the_parser_not_by_its_spelling(runtime, agent):
    output = {"prefix, remaining: 7, suffix": False, "remaining": 7}
    case = lookup_case(output, {"remaining": 7})
    laid, session, summary, shown = run(case, runtime, agent=agent)
    seq, field, start, end = contract._source(laid, case["facts"][0])
    line = '"prefix, remaining: 7, suffix": false, remaining: 7'
    assert (start, end) == (39, 51) and line[start:end] == "remaining: 7"
    assert contract.score(laid, summary, shown)["complete"]
    padded = {"prefix, remaining: 7, suffix": False} | {f"c{n:02}": n for n in range(60)}
    case = lookup_case(padded | {"remaining": 7}, {"remaining": 7})  # now in a later piece
    laid, session, summary, shown = run(case, runtime, agent=agent)
    piece = contract.served_piece(laid, case["facts"][0])[2]
    assert piece.endswith("remaining: 7") and "prefix" not in piece
    assert contract.score(laid, summary, shown)["complete"], shown["text"]
