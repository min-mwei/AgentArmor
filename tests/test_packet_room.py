"""One budget rule for a packet, offline and live (agentarmor_monitor.policy.packet_room), CPU only.

The server's request gives the packet what the rules, the host facts and the call leave of
--max-chars (escalate.detect). The offline render (training/summarizer/campaign_arms.py) gave the
packet the whole budget, and panels then put that packet in a state with the rules and the call:
a history the render called ready and complete was, in the server's request of the same budget,
left without room for key evidence (development file 006, restart placement: history_incomplete,
key_group_left_out live; asked, ready and complete offline). Both now take the packet's room from
packet_room(), so a packet ready offline fits the request a server would build.

The negative control first: given the whole budget, as before, packets panels put in a state
exceed the budget. All campaigns are the tracked synthetic starters.
"""

import json
import sys
from pathlib import Path

import pytest

TRAINING = str(Path(__file__).resolve().parents[1] / "training" / "summarizer")
if TRAINING not in sys.path:
    sys.path.insert(0, TRAINING)

import campaign_arms as arms  # noqa: E402
from agentarmor_monitor import escalate, panels  # noqa: E402
from agentarmor_monitor.policy import load_rules, packet_room, state  # noqa: E402

STARTER = Path(TRAINING) / "data" / "starter" / "campaigns-v2.jsonl"
BUDGET = 3000


def test_the_room_is_what_the_rules_and_the_call_leave_and_a_packet_of_it_fills_the_state():
    rules = load_rules()
    tool, shown, _ = escalate.proposed({"tool": "Bash"}, arguments={"command": "make test"})
    room = packet_room(rules, (tool, shown), BUDGET)
    assert 0 < room < BUDGET
    assert len(state(rules, "x" * room, (tool, shown))) == BUDGET
    assert packet_room(rules, (tool, shown + "y" * 50), BUDGET) == room - 50  # the call is whole


def test_the_server_builds_its_packet_in_that_room(monkeypatch):
    # escalate.detect asks for the packet in packet_room(), and in nothing it works out itself.
    asked = []

    def packet(summary, entry, max_chars, sessions=None):
        asked.append(max_chars)
        raise RuntimeError("stop")

    monkeypatch.setattr(escalate, "packet", packet)
    rules = load_rules()
    summary = {"session": {"agent": "claude_code", "session_id": "s", "schema": "summarizer/v2"}}
    entry = {"type": "action", "seq": 3, "tool": "Bash", "call_id": "c1", "text": "make test"}
    with pytest.raises(RuntimeError, match="stop"):
        escalate.detect(None, summary, entry, BUDGET, rules, arguments={"command": "make test"})
    tool, shown, _ = escalate.proposed(entry, {"command": "make test"}, None)
    assert asked == [packet_room(rules, (tool, shown), BUDGET)]


def write(tmp_path, *options):
    """campaign_arms write of the starters at BUDGET, then panels of its packets."""
    out = tmp_path / ("render" + "".join(options).replace("-", "_"))
    command = ["write", str(STARTER), "--out", str(out), "--budget", str(BUDGET), *options]
    assert arms.main(command) == 0
    rows = [json.loads(line) for line in (out / "packets.jsonl").read_text().splitlines()]
    made = tmp_path / ("panels" + "".join(options).replace("-", "_"))
    panels.write(out / "packets.jsonl", made)
    states = {}
    for arm in arms.ARMS:
        for line in (made / f"{arm}.jsonl").read_text().splitlines():
            meta = json.loads(line)["_meta"]
            states[(meta["run"], arm)] = meta["state_chars"]
    roster = json.loads((out / "roster.json").read_text())
    return rows, states, roster


def test_a_packet_given_the_whole_budget_does_not_fit_the_request_it_is_put_in(tmp_path, capsys):
    # The negative control, the render as it was: the packet has all of the budget.
    rows, states, roster = write(tmp_path, "--budget-of", "packet")
    capsys.readouterr()
    assert roster["budget_of"] == "packet"
    assert all(row["packet_budget"] == BUDGET for row in rows)
    over = [key for key, chars in states.items() if chars > BUDGET]
    ready = [(r["run"], r["arm"]) for r in rows if r["ready"] and arms.fits(r)]
    assert set(over) & set(ready)  # called ready and within its budget, and too large to send


def test_the_render_builds_every_packet_in_the_server_s_room(tmp_path, capsys):
    rows, states, roster = write(tmp_path)  # the default: the request's budget
    capsys.readouterr()
    assert roster["budget_of"] == "request" and roster["budget"] == BUDGET
    rules = load_rules()
    for row in rows:
        tool, shown, _ = escalate.proposed({"tool": row["tool"]}, arguments=row["input"])
        assert row["packet_budget"] == packet_room(rules, (tool, shown), BUDGET)
        assert row["budget"] == BUDGET
        # A packet within its room is a state within the budget, as the server would send it.
        if arms.fits(row):
            assert states[(row["run"], row["arm"])] <= BUDGET
    assert all(arms.fits(row) for row in rows if row["arm"] == "history")


def builder_rows(capsys, *options):
    """history_builder's own command on the starters at BUDGET, its rows and what it printed."""
    import history_builder

    assert history_builder.main([str(STARTER), "--budget", str(BUDGET), "--rows", *options]) == 0
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    rows = [line for line in printed if "id" in line and "agent" in line]
    return rows, printed[-1], [line for line in printed if "builder" in line and "id" not in line]


def test_the_history_builder_s_command_builds_its_packets_in_the_server_s_room(capsys):
    rows, told, counts = builder_rows(capsys)  # the default: the request's budget
    assert told["budget_of"] == "request" and told["budget"] == BUDGET
    assert counts and all(line["budget_of"] == "request" for line in counts)
    rules = load_rules()
    campaigns = {c["id"]: c for c in arms.read_jsonl(STARTER)}
    for row in rows:
        laid = arms.cc.layout(campaigns[row["id"]], row["agent"])
        call = arms.history_builder.proposed_call(laid)
        tool, shown, _ = escalate.proposed(
            {"tool": call.get("tool_name")}, arguments=call.get("tool_input")
        )
        assert row["packet_budget"] == packet_room(rules, (tool, shown), BUDGET) < BUDGET
        assert row["budget"] == BUDGET


def test_the_history_builder_s_command_keeps_the_whole_budget_reading_and_says_so(capsys):
    rows, told, counts = builder_rows(capsys, "--budget-of", "packet")
    assert told["budget_of"] == "packet" and all(line["budget_of"] == "packet" for line in counts)
    assert rows and all(row["packet_budget"] == row["budget"] == BUDGET for row in rows)
