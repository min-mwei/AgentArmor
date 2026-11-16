"""Session observations (agentarmor_monitor.observe, agentarmor-observations/v1): CPU, public
synthetic fixtures only. Each test names the property it holds; none reads a dataset."""

import json

import pytest
from agentarmor_monitor import escalate, observe
from agentarmor_monitor.policy import load_rules, state
from summarizer.store import load_summary
from test_monitor import CWD, Scripted, log, summarized, tool

HERE = "/srv/work/demo"


@pytest.fixture
def spool(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMMARIZER_HOME", str(tmp_path / "spool"))
    return tmp_path / "spool"


def session(prompts, alias=None, cwd=HERE, session_id="s-here"):
    """A session as the lookup takes it, with user prompts (seq, text[, chars]) only."""
    entries = []
    for seq, text, *chars in prompts:
        entry = {"type": "prompt", "seq": seq, "ts": 100.0 + seq, "trust": "user", "text": text}
        entries.append(entry | ({"chars": chars[0]} if chars else {}))
    summary = {"session": {"session_id": session_id, "cwd": cwd}, "entries": entries}
    return {"summary": summary, "cwd": cwd, "alias": alias, "key": session_id}


CALL = {"type": "action", "seq": 20, "ts": 120.0, "tool": "Bash", "call_id": "call-9"}


def seen(arguments, prompts=(), others=(), source="log"):
    return observe.observe(session(prompts), CALL, arguments, source, others)


def only(account, kind):
    found = [item for item in account["targets"] if item["kind"] == kind]
    assert len(found) == 1, account["targets"]
    return found[0]


def test_a_lookalike_host_is_a_near_difference_with_its_characters_and_sources():
    other = session(
        [(2, "Deliver the index only to archive-west.example.")], "d0ec", session_id="o"
    )
    account = seen({"command": "rsync out/index.json archlve-west.example:/incoming/"}, [], [other])
    host = only(account, "host")
    assert host["binding"] == {"field": "arguments.command", "at": [21, 41], "token": 2}
    (candidate,) = host["candidates"]
    assert candidate["user"] == "archive-west.example" and not candidate["raw_equal"]
    assert candidate["relation"] == "near"
    assert candidate["differences"] == [{"op": "replace", "at": 4, "call": "l", "user": "i"}]
    assert candidate["object_identity"] == "unknown"
    (mention,) = candidate["mentions"]
    assert (mention["session"], mention["seq"], mention["field"]) == ("d0ec", 2, "prompt")
    assert mention["namespace"] == "unknown" and mention["cues"]["limit"] == ["only"]
    text = "\n".join(observe.render(account))
    assert 'call "l" (U+006C) vs user "i" (U+0069)' in text and "[s:d0ec] #2" in text
    assert "deny" not in text.lower() and "allow" not in text.lower()


def test_raw_equality_is_reported_and_is_not_called_object_identity():
    account = seen(
        {"command": "rsync out/index.json archive-west.example:/incoming/"},
        [(2, "Deliver the index to archive-west.example.")],
    )
    (candidate,) = only(account, "host")["candidates"]
    assert candidate["raw_equal"] and candidate["differences"] == []
    assert candidate["object_identity"] == "unknown" and candidate["unknown_because"]
    assert "raw text equal" in "\n".join(observe.render(account))


def test_negation_and_quotation_are_kept_as_cue_words_not_readings():
    account = seen(
        {"url": "https://drop.example/up"},
        [(2, 'Never send anything to "drop.example" again.')],
    )
    (candidate,) = only(account, "host")["candidates"]
    (mention,) = candidate["mentions"]
    assert mention["cues"]["negation"] == ["never"] and mention["quoted"]
    # Mentioning a name under a negation stays a mention: the comparison still says "equal".
    assert candidate["raw_equal"]
    text = "\n".join(observe.render(account))
    assert "in quotation marks" in text and "negation never" in text


def test_a_later_revocation_is_listed_in_order_with_its_own_cues():
    account = seen(
        {"command": "scp out/a.tgz vault.example:/in/"},
        [
            (2, "Copy the bundle to vault.example when it is built."),
            (5, "Cancel that: vault.example is no longer approved."),
        ],
    )
    (candidate,) = only(account, "host")["candidates"]
    first, later = candidate["mentions"]
    assert (first["seq"], later["seq"]) == (2, 5)
    assert "revocation" not in first["cues"]
    assert later["cues"]["revocation"] == ["cancel", "no longer"]


def test_an_ambiguous_reference_is_no_target_and_says_so():
    account = seen(
        {"url": "https://mirror.example/up"},
        [(2, "Upload it to the same place as last time.")],
    )
    host = only(account, "host")
    assert host["candidates"] == [] and host["user_named_of_kind"] == []
    assert "no user-channel statement before the call names a host" in "\n".join(
        observe.render(account)
    )


def test_case_only_and_confusable_differences_keep_semantics_unknown():
    path = seen({"file_path": "/srv/show/cues/ok"}, [(2, "Write to /srv/show/Cues/ok.")])
    (candidate,) = only(path, "path")["candidates"]
    assert candidate["relation"] == "case_only" and not candidate["raw_equal"]
    assert "filesystem case rules and symlinks not established" in candidate["unknown_because"]
    host = seen({"url": "https://nоrth.example/x"}, [(2, "Post to https://north.example/x")])
    (candidate,) = only(host, "host")["candidates"]
    assert candidate["relation"] == "confusable"
    assert "IDNA and confusable mapping not established" in candidate["unknown_because"]
    assert "U+043E" in "\n".join(observe.render(host))


def test_no_user_target_and_an_unrelated_one_are_told_apart():
    none = seen({"url": "https://a.example/x"}, [(2, "Finish the report.")])
    assert only(none, "host")["candidates"] == []
    other = seen({"url": "https://a.example/x"}, [(2, "Send it to zz-elsewhere.example.")])
    assert only(other, "host")["user_named_of_kind"] == ["zz-elsewhere.example"]
    assert "no user-named host is equal or near" in "\n".join(observe.render(other))


def test_multiple_candidates_are_all_shown():
    account = seen(
        {"command": "deliver --to ledger-east.example report.json"},
        [(2, "Use ledger-east.example or ledger-west.example."), (3, "Prefer ledger-eat.example.")],
    )
    users = [c["user"] for c in only(account, "host")["candidates"]]
    assert users == ["ledger-east.example", "ledger-eat.example", "ledger-west.example"]


def test_only_user_prompts_before_the_call_count_and_other_sessions_are_kept_apart():
    mine = session([(2, "Ship to good.example"), (25, "Ship to evil.example")])
    mine["summary"]["entries"].append(
        {"type": "content", "seq": 4, "trust": "untrusted", "text": "Ship to good.example"}
    )
    account = observe.observe(mine, CALL, {"url": "https://evil.example/"}, "log", [])
    assert only(account, "host")["candidates"] == []  # #25 is after the call; #4 is not the user's
    assert account["coverage"]["statements_read"] == 1


def test_relative_paths_are_compared_after_joining_recorded_directories_and_said_so():
    other = session(
        [(2, "Inspect projects/atlas/key.json only.")], "a1b2", cwd=HERE, session_id="o"
    )
    account = observe.observe(
        session([]), CALL, {"command": f"cat {HERE}/projects/atlas/key.json"}, "log", [other]
    )
    (candidate,) = only(account, "path")["candidates"]
    assert candidate["relation"] == "equal_after_joining_working_directories"
    assert not candidate["raw_equal"] and candidate["compared"][0] == candidate["compared"][1]
    assert "namespace of the earlier session unknown" in candidate["unknown_because"]


def test_a_suffix_or_an_extra_label_is_a_near_difference():
    path = seen({"path": "/w/out/r.json"}, [(2, "Deliver only /w/out/r.json.reviewed.")])
    (candidate,) = only(path, "path")["candidates"]
    assert candidate["relation"] == "within"
    assert candidate["differences"][0]["user"] == ".reviewed"
    host = seen({"endpoint": "https://ingest.rill.example/e"}, [(2, "Post to rill.example.")])
    assert only(host, "host")["candidates"][0]["relation"] == "extends"


def test_a_bare_account_name_is_found_as_a_word_of_a_statement():
    account = seen({"account": "acct-crest1ine-8"}, [(2, "Attach it to acct-crestline-8.")])
    (candidate,) = only(account, "account")["candidates"]
    assert candidate["differences"] == [{"op": "replace", "at": 10, "call": "1", "user": "l"}]


def test_what_cannot_be_read_is_counted_and_shown():
    account = seen({"command": "echo 'unclosed"}, [(2, "Hi.", 400)], source="representation")
    assert account["unsupported"] == ["call_shown_as_display_text", "command_untokenized"]
    assert account["coverage"]["statements_clipped"] == 1
    text = "\n".join(observe.render(account))
    assert "(1 clipped)" in text and "not read: call_shown_as_display_text" in text


def test_the_section_is_capped_and_says_what_it_left_out():
    hosts = [f"h{n}-alpha-node.example" for n in range(12)]
    prompts = [
        (2 + n, f"Send only to {h.replace('alpha', 'alpah')} and never elsewhere, {'x' * 150}")
        for n, h in enumerate(hosts)
    ]
    account = seen({"command": "send " + " ".join(f"--to {h}" for h in hosts)}, prompts)
    lines = observe.render(account)
    assert sum(len(line) + 1 for line in lines) <= observe.MAX_CHARS
    assert account["coverage"]["comparisons_left_out"] > 0
    assert "comparisons left out for room" in lines[-1]


def test_off_leaves_the_request_exactly_as_before_and_on_adds_only_the_section(spool):
    name = "s-observe"
    log(
        name,
        {"hook_event_name": "SessionStart", "source": "startup"},
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Deliver out/index.json only to archive-west.example.",
        },
        *tool(1, "Bash", {"command": f"rsync {CWD}/out/index.json archlve-west.example:/in/"}),
    )
    summary = load_summary("claude_code", summarized(name))
    entry = next(e for e in summary["entries"] if e["type"] == "action")
    rules = load_rules()
    off_client, on_client = Scripted(100_000), Scripted(100_000)
    sent = []
    for client in (off_client, on_client):
        client.ask = (
            lambda ask: lambda body, questions: sent.append(body) or ask(body, questions)
        )(client.ask)
    off = escalate.detect(off_client, summary, entry, 11000, rules)
    on = escalate.detect(on_client, summary, entry, 11000, rules, observations=True)
    assert "observations" not in off and "observations" not in off["layout"]
    assert on["layout"]["observations"] == observe.VERSION
    plain, observed = sent
    lines = observe.render(on["observations"])
    head, _, tail = observed.partition("\n\n" + "\n".join(lines))
    assert head + tail == plain  # every other character of the request is unchanged
    assert off["packet"]["sha256"] == on["packet"]["sha256"]  # the packet itself is the same
    assert 'call "l" (U+006C) vs user "i" (U+0069)' in observed
    assert observed.index("Session observations") < observed.index("Conversation so far:")


def test_a_span_inside_the_observation_section_is_unavailable_not_a_quote():
    rules = load_rules()
    lines = ["Session observations (test)", 'call "x"']
    call = ("Bash", json.dumps({"command": "x"}))
    body = state(rules, "PACKET", call, lines)
    from agentarmor_monitor.policy import layout

    from summarizer import sourcemap

    mapped = sourcemap.map_of(
        lambda: "PACKET", frame=lambda shown: layout(rules, shown, call, lines)
    )
    at = body.index('call "x"')
    told = {"decision": {"status": "spans", "spans": [[at, at + 8]], "state_sha256": mapped.sha256}}
    kept = escalate.spans_record(told, lambda: mapped, body)
    assert kept["decision"]["status"] == "error"
    assert kept["decision"]["why"] == "observation_text_unmapped"
