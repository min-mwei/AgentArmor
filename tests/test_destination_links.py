"""A call's destinations are linked to earlier evidence whole, through the monitor's own packet
(MR308 P1). CPU only; inert synthetic hook events, nothing represented here runs."""

import pytest

pytest.importorskip("summarizer")

from agentarmor_monitor.escalate import packet  # noqa: E402
from summarizer.embedder import HashEmbedder  # noqa: E402
from summarizer.hook import build_record  # noqa: E402
from summarizer.store import view  # noqa: E402
from summarizer.summarize import Runtime, Session  # noqa: E402

CWD, HOME = "/home/dev/proj", "/home/dev"


def events(agent, count):
    """The reviewer's control: a send to count recipients after a check of the last of them."""
    folder = ".claude" if agent == "claude_code" else ".codex"
    common = {"session_id": "s-1", "cwd": CWD, "permission_mode": "default"}
    common["transcript_path"] = f"{HOME}/{folder}/projects/proj/s-1.jsonl"
    if agent == "codex_code":
        common |= {"model": "gpt-test", "turn_id": "turn-1"}
    made, calls = [], 0

    def emit(name, **fields):
        made.append({**common, "hook_event_name": name, **fields})

    def tool(name, given, response=None):
        nonlocal calls
        calls += 1
        call = {"tool_name": name, "tool_input": given, "tool_use_id": f"call-{calls}"}
        emit("PreToolUse", **call)
        if response is not None:
            emit("PostToolUse", **call, tool_response=response)

    addresses = [f"user{index}@dept{index}.example" for index in range(count)]
    emit("SessionStart", source="startup")
    emit("UserPromptSubmit", prompt="Send the weekly report to the listed recipients after review.")
    book = "\n".join(f"address={a} label=team-{i}" for i, a in enumerate(addresses))
    tool("Bash", {"command": "cat mail/directory.txt"}, book)
    tool(
        "Bash",
        {"command": f"python tools/check_recipients.py {addresses[-1]}"},
        "recipient=mb-809; status=verified",
    )
    draft = f"to: {' '.join(addresses)}\nsubject: Weekly report\nbody_file: out/report.md"
    tool("Bash", {"command": "cat out/draft.txt"}, draft)
    for index in range(14):
        tool("Bash", {"command": f"python tools/lint_{index}.py"}, f"lint {index} fine")
    tool(
        "mcp__mail__send",
        {"to": addresses, "subject": "Weekly report", "body_file": "out/report.md"},
    )
    return made, f"call-{calls}"


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("count", [2, 9, 40])
def test_a_ready_packet_shows_the_check_of_any_recipient(agent, count):
    # At 0700814, nine recipients gave ready=true, scanned_all_captured=true, call=verified and no
    # check result: the dependency index was the eight observables an entry shows.
    made, call = events(agent, count)
    session = Session(agent, "test", Runtime(HashEmbedder(), strict=True))
    for event in made:
        session.add(build_record(agent, event))
    session.flush()
    summary = view(session.summary())
    target = next(e for e in summary["entries"] if e["type"] == "action" and e["call_id"] == call)
    for budget in (4000, 20000):
        text, account, _ = packet(summary, target, budget)
        assert account["call"] == "verified" and account["scanned_all_captured"] is True
        assert "mb-809" in text or account["ready"] is False, (budget, account)
    text, account, _ = packet(summary, target, 20000)
    assert "mb-809" in text and account["ready"] is True  # linked and shown, with room


def key_listing(agent, count):
    """The reviewer's control (MR309 P2b): a listing that names count key files and no place."""
    folder = ".claude" if agent == "claude_code" else ".codex"
    common = {"session_id": "s-2", "cwd": CWD, "permission_mode": "default"}
    common["transcript_path"] = f"{HOME}/{folder}/projects/proj/s-2.jsonl"
    if agent == "codex_code":
        common |= {"model": "gpt-test", "turn_id": "turn-1"}
    command = "ls " + " ".join(f"{HOME}/.ssh/id_rsa_{index}" for index in range(count))
    call = {"tool_name": "Bash", "tool_input": {"command": command}, "tool_use_id": "call-1"}
    return [
        {**common, "hook_event_name": "SessionStart", "source": "startup"},
        {
            **common,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "List the local key file names only",
        },
        {**common, "hook_event_name": "PreToolUse", **call},
    ], "call-1"


def summarized(agent, made):
    session = Session(agent, "test", Runtime(HashEmbedder(), strict=True))
    for event in made:
        session.add(build_record(agent, event))
    session.flush()
    return view(session.summary())


@pytest.mark.parametrize("agent", ["claude_code", "codex_code"])
@pytest.mark.parametrize("count", [1, 9])
def test_a_call_that_names_no_place_has_its_whole_list_of_places(agent, count):
    # At 067dfd0, nine key paths (eight shown observables, none a place) gave ready=false,
    # key_dropped=1, "places of 1 calls are not all listed": an empty list of places was not
    # written, and a reader took the entry for a summary that kept no such list.
    summary = summarized(agent, key_listing(agent, count)[0])
    (target,) = [e for e in summary["entries"] if e["type"] == "action"]
    assert target["places"] == [] and "places_complete" not in target
    text, account, _ = packet(summary, target, 20000)
    assert account["ready"] is True and account["key_dropped"] == 0, account
    assert "not all listed" not in text


def test_a_summary_that_kept_no_list_of_places_stays_conservative():
    # A legacy entry (no "places" field) showing as many observables as an entry can show may
    # name more places than it shows; one with a clipped input or past the cap is never whole.
    from summarizer import history, text

    legacy = {"iocs": [f"path:{HOME}/.ssh/id_rsa_{index}" for index in range(8)]}
    clipped = {"places": [], "places_complete": False}
    over = {"places": ["domain:a.example"], "places_complete": False, "places_not_listed": 3}
    for whole in (text._places_whole, history._places_whole):
        assert whole({"places": []}) is True and whole(legacy | {"places": []}) is True
        assert whole(legacy) is False and whole(clipped) is False and whole(over) is False
