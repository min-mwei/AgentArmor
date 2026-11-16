"""Cumulative-quantity observations (agentarmor_monitor.quantities, agentarmor-quantities/v2): CPU,
public synthetic fixtures only. Negative controls first; none reads a dataset."""

import pytest
from agentarmor_monitor import observe, quantities
from agentarmor_monitor.decide import Decider

HERE = "/srv/work/demo"
CALL = {"type": "action", "seq": 30, "ts": 130.0, "tool": "Bash", "call_id": "call-9"}
SEND = {
    "command": "python3 tools/ship-send.py --to https://intake.example/in data/r5.json data/r6.json"
}


def session(entries, alias=None, cwd=HERE, session_id="s-here"):
    summary = {"session": {"session_id": session_id, "cwd": cwd}, "entries": entries}
    return {"summary": summary, "cwd": cwd, "alias": alias, "key": session_id}


def said(seq, text, trust="user"):
    return {"type": "prompt", "seq": seq, "ts": 100.0 + seq, "trust": trust, "text": text}


def ran(seq, command, status="returned", call_id=None):
    call_id = call_id or f"c{seq}"
    return {
        "type": "action",
        "seq": seq,
        "ts": 100.0 + seq,
        "tool": "Bash",
        "status": status,
        "target": command,
        "text": f"Bash: {command}",
        "call_id": call_id,
    }


def back(seq, text, call_id, **more):
    return {
        "type": "content",
        "seq": seq,
        "ts": 100.0 + seq,
        "trust": "untrusted",
        "text": text,
        "call_id": call_id,
        "source": "shell",
    } | more


def measured(mine, others=(), arguments=SEND):
    return quantities.quantities(mine, CALL, arguments, others)


def text_of(mine, others=(), arguments=SEND):
    account = observe.observe(mine, CALL, arguments, "log", others, quantities=True)
    return account, "\n".join(observe.render(account))


CAP = "Across this incident, send no more than five records outside the project."
SENT = [
    ran(4, "python3 tools/ship-send.py data/r1.json data/r2.json data/r3.json data/r4.json"),
    back(5, "four sanitized records accepted", "c4"),
]


def test_no_cap_stated_gives_no_cap():
    assert measured(session([said(2, "Send the next group of records."), *SENT]))["caps"] == []


def test_a_cap_in_untrusted_text_is_not_a_cap():
    mine = session([said(2, "Continue."), back(3, CAP, "c1"), *SENT])
    assert measured(mine)["caps"] == []


def test_a_cap_on_another_unit_counts_nothing_of_this_one():
    mine = session([said(2, "Send no more than five cover sheets."), *SENT])
    (cap,) = measured(mine)["caps"]
    assert cap["unit_words"] == ["cover", "sheet"] and cap["contributions"] == []
    assert cap["total"]["at_least"] == 0


def test_a_cap_raised_or_revoked_later_is_listed_in_order():
    mine = session(
        [
            said(2, CAP),
            said(3, "You may send at most 9 records now."),
            said(6, "Cancel the records limit."),
            *SENT,
        ]
    )
    first = measured(mine)["caps"][0]
    assert [(x["seq"], x["caps"], x["revocation_words"]) for x in first["later"]] == [
        (3, [9], []),
        (6, [], ["cancel"]),
    ]


def test_contributions_of_another_directory_are_listed_and_not_counted():
    far = session(SENT, alias="f00d", cwd="/elsewhere", session_id="o1")
    near = session(SENT, alias="0a0a", cwd=HERE, session_id="o2")
    cap = measured(session([said(2, CAP)]), [far])["caps"][0]
    assert cap["contributions"][0]["counted_here"] is False and cap["total"]["at_least"] == 0
    cap = measured(session([said(2, CAP)]), [near])["caps"][0]
    assert cap["contributions"][0]["session"] == "0a0a" and cap["total"]["at_least"] == 4


def test_a_clipped_result_leaves_the_upper_bound_unknown():
    clipped = [SENT[0], back(5, "four sanitized records accepted; more", "c4", text_chars=900)]
    cap = measured(session([said(2, CAP), *clipped]))["caps"][0]
    assert cap["total"]["at_least"] == 4 and cap["total"]["at_most"] is None
    assert any("clipped" in why for why in cap["total"]["unknown_because"])


def test_an_attempt_without_a_result_is_not_an_outcome():
    tried = [ran(4, "python3 tools/ship-send.py data/r1.json", status="proposed")]
    cap = measured(session([said(2, CAP), *tried]))["caps"][0]
    assert cap["contributions"] == [] and cap["total"]["at_least"] == 0
    assert cap["total"]["at_most"] is None
    assert "attempted, no result returned (proposed)" in cap["total"]["unknown_because"][0]


def test_a_call_that_returned_with_no_quantity_leaves_the_upper_bound_unknown():
    quiet = [ran(4, "python3 tools/ship-send.py data/r1.json"), back(5, "done", "c4")]
    cap = measured(session([said(2, CAP), *quiet]))["caps"][0]
    assert (
        cap["total"]["at_most"] is None
        and "no quantity stated" in cap["total"]["unknown_because"][0]
    )


def test_a_check_that_names_a_number_is_no_report():
    checked = [ran(4, "python3 tools/check.py data"), back(5, "four records pass checks", "c4")]
    assert measured(session([said(2, CAP), *checked]))["caps"][0]["contributions"] == []


def test_a_file_that_repeats_a_report_widens_the_bounds_and_says_so():
    read = [ran(6, "cat reports/result.txt"), back(7, "The first four records were sent.", "c6")]
    cap = measured(session([said(2, CAP), *SENT, *read]))["caps"][0]
    assert cap["total"] | {"unknown_because": []} == {
        "at_least": 4,
        "at_most": 8,
        "unknown_because": [],
        "overlap": [
            "1 report(s) not tied to a call of the operation may repeat another: "
            "each enters the lower bound only alone"
        ],
        "assumes": quantities.ASSUMES,
        "calls_with_reports": 1,
        "reports_not_tied_to_a_call": 1,
    }
    assert [c["of"] for c in cap["contributions"]] == ["result of a call", "text read from a file"]


def test_the_full_shape_is_stated_as_numbers_and_never_as_a_verdict():
    account, text = text_of(session([said(2, CAP), *SENT]))
    (cap,) = account["quantities"]["caps"]
    assert (cap["n"], cap["op"], cap["scope"]) == (5, "send", "Across this incident")
    assert cap["proposed"] == {"count": 2, "how": "2 path operand(s) of its command"}
    assert "at least 4 + 2 = 6 beside the stated number 5" in text
    for word in ("exceed", "over the", "violat", "deny", "allow"):
        assert word not in text.lower()


def test_cap_forms_and_counts_by_a_named_option():
    (cap,) = quantities.caps_in(
        "Cap externally delivered recovery bundles at one during this window."
    )
    assert (cap["n"], cap["op"], cap["unit_words"]) == (
        1,
        "send",
        ["bundle", "externally", "recovery"],
    )
    arguments = {"command": "escrowctl send --bundle b/x.tgz --to https://e.example/x"}
    assert quantities.call_count(arguments, cap["unit_words"]) == (
        1,
        "1 value(s) of option --bundle",
    )
    (cap,) = quantities.caps_in(
        "Remove no more than three snapshot directories across this window."
    )
    assert (cap["op"], cap["unit"]) == ("delete", "snapshot directories")
    assert (
        quantities.call_count({"command": "rm -rf a/x a/y && echo ok"}, cap["unit_words"])[0] == 2
    )


def test_operation_comes_from_the_program_not_from_a_file_it_reads():
    assert quantities.op_of_call(ran(1, "cat reports/transfer-result.txt")) is None
    assert quantities.op_of_call(ran(1, "python3 tools/x-send.py a.json")) == "send"
    assert quantities.op_of_call(ran(1, "escrowctl send --bundle a")) == "send"
    assert quantities.op_of_call(ran(1, "rm -rf cache/a")) == "delete"
    assert quantities.op_of_call({"tool": "Write", "target": "/w/transfer-plan.txt"}) is None


def test_off_leaves_part_a_byte_for_byte():
    mine = session([said(2, CAP), *SENT])
    plain = observe.observe(mine, CALL, SEND, "log", [])
    assert "quantities" not in plain
    with_b = observe.observe(mine, CALL, SEND, "log", [], quantities=True)
    assert "\n".join(observe.render(plain)) == "\n".join(
        observe.render(observe.observe(mine, CALL, SEND, "log", []))
    )
    assert "cap stated" in "\n".join(observe.render(with_b))
    assert "cap stated" not in "\n".join(observe.render(plain))


def test_quantities_need_the_observations_switch():
    with pytest.raises(ValueError):
        Decider(None, quantities=True)
    assert Decider(None, observations=True, quantities=True).about()["observation_quantities"] == (
        quantities.VERSION
    )


def test_a_retry_naming_the_same_operands_is_not_summed_into_the_lower_bound():
    retry = [
        ran(4, "python3 tools/ship-send.py data/r1.json data/r2.json"),
        back(5, "two records sent", "c4"),
        ran(6, "python3 tools/ship-send.py data/r1.json data/r2.json"),
        back(7, "two records sent", "c6"),
    ]
    total = measured(session([said(2, CAP), *retry]))["caps"][0]["total"]
    assert (total["at_least"], total["at_most"]) == (2, 4)
    distinct = [
        ran(4, "python3 tools/ship-send.py data/r1.json"),
        back(5, "one record sent", "c4"),
        ran(6, "python3 tools/ship-send.py data/r2.json"),
        back(7, "one record sent", "c6"),
    ]
    total = measured(session([said(2, CAP), *distinct]))["caps"][0]["total"]
    assert (total["at_least"], total["at_most"]) == (2, 2)


def test_a_partial_failure_counts_what_was_reported_sent_not_the_denominator():
    partial = [
        ran(4, "python3 tools/ship-send.py data/"),
        back(5, "3 of 5 records sent; 2 failed", "c4"),
    ]
    cap = measured(session([said(2, CAP), *partial]))["caps"][0]
    assert [c["n"] for c in cap["contributions"]] == [3]
    assert (cap["total"]["at_least"], cap["total"]["at_most"]) == (3, 3)


def test_scope_words_are_kept_as_said_and_the_window_is_not_resolved():
    (cap,) = quantities.caps_in("During this rotation window, send at most 2 bundles.")
    assert cap["scope"] == "During this rotation window"
    _, text = text_of(session([said(2, CAP), *SENT]))
    assert 'scope words "Across this incident" (window not resolved)' in text
    assert "observed effects: none established by this server" in text


def test_no_cap_stated_adds_nothing_to_the_section():
    mine = session([said(2, "Send the next group of records."), *SENT])
    plain = observe.observe(mine, CALL, SEND, "log", [])
    with_b = observe.observe(mine, CALL, SEND, "log", [], quantities=True)
    assert observe.render(plain) == observe.render(with_b)


# MR303 (second P1): only a small set of affirmative forms is arithmetic; unknown overlap and
# several reports in one result never add into a guaranteed lower bound.

ONE_CALL = ran(4, "python3 tools/ship-send.py data/r1.json data/r2.json data/r3.json data/r4.json")


def one_result(text):
    cap = measured(session([said(2, CAP), ONE_CALL, back(5, text, "c4")]))["caps"][0]
    return cap, cap["total"]


@pytest.mark.parametrize(
    ("text", "n", "form"),
    [
        ("four records sent", 4, "number unit verb"),
        ("0 records sent", 0, "number unit verb"),
        ("sent 4 records", 4, "verb number unit"),
        ("4 records were delivered", 4, "number unit verb"),
        ("3 of 5 records sent; 2 failed", 3, "number unit verb"),
    ],
)
def test_supported_affirmative_forms_are_counted(text, n, form):
    cap, total = one_result(text)
    assert [(c["n"], c["form"], c["unresolved"]) for c in cap["contributions"]] == [(n, form, None)]
    assert (total["at_least"], total["at_most"], total["unknown_because"]) == (n, n, [])


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("four records not sent", "negation"),
        ("four records would be sent", "modal"),
        ("failed to send 4 records", "failure"),
        ("will send 4 records", "modal"),
        ("can send up to 4 records", "modal"),
        ("if approved, 4 records sent", "condition"),
        ("four records sent?", "question"),
        ("4 records sending", "verb"),
        ("four records almost sent", "hedge"),
        ("None of the four records were sent", "negation"),
        ("4 records weren't sent", "negation"),
    ],
)
def test_a_number_before_or_after_a_send_verb_is_not_by_itself_a_report(text, kind):
    cap, total = one_result(text)
    (c,) = cap["contributions"]
    assert c["n"] == 4 and c["form"] is None and c["unresolved"].startswith(kind)
    assert c["sentence"] == text.rstrip("?")  # the source sentence stays as evidence
    assert total["at_least"] == 0 and total["at_most"] is None
    assert any("not counted" in why and kind in why for why in total["unknown_because"])


def test_no_number_is_no_report_and_the_upper_bound_stays_unknown():
    cap, total = one_result("no records sent")
    assert cap["contributions"] == []
    assert total["at_least"] == 0 and total["at_most"] is None


def test_calls_with_no_operand_identity_are_not_added_into_the_lower_bound():
    bundle = [
        ran(4, "escrowctl send --bundle batch1"),
        back(5, "one record sent", "c4"),
        ran(6, "escrowctl send --bundle batch1"),
        back(7, "one record sent", "c6"),
    ]
    total = measured(session([said(2, CAP), *bundle]))["caps"][0]["total"]
    assert (total["at_least"], total["at_most"]) == (1, 2)
    assert any("no path operand" in note for note in total["overlap"])
    named = [
        ran(4, "escrowctl send --bundle b/one.tgz"),
        back(5, "one record sent", "c4"),
        ran(6, "escrowctl send --bundle b/two.tgz"),
        back(7, "one record sent", "c6"),
    ]
    total = measured(session([said(2, CAP), *named]))["caps"][0]["total"]
    assert (total["at_least"], total["at_most"]) == (2, 2)
    mixed = [named[0] | {}, back(5, "two records sent", "c4"), *bundle[2:]]
    total = measured(session([said(2, CAP), *mixed]))["caps"][0]["total"]
    assert (total["at_least"], total["at_most"]) == (2, 3)


def test_several_reports_in_one_result_keep_the_upper_bound():
    cap, total = one_result("two records sent in batch alpha; three records sent in batch beta")
    assert [c["n"] for c in cap["contributions"]] == [2, 3]
    assert (total["at_least"], total["at_most"]) == (3, 5)
    assert any("2 reports" in note for note in total["overlap"])
    cap, total = one_result("three records sent in batch beta")
    assert (total["at_least"], total["at_most"], total["overlap"]) == (3, 3, [])


def test_the_section_prints_unresolved_reports_and_the_v2_format():
    _, text = text_of(session([said(2, CAP), ONE_CALL, back(5, "four records not sent", "c4")]))
    assert quantities.VERSION == "agentarmor-quantities/v2"
    assert "not counted: unresolved (negation word not)" in text
    assert "at least 0, at most unknown" in text
    assert "at least 0 + 2 = 2 beside the stated number 5" in text
