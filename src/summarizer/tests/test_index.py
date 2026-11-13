"""The search index: what the daemon writes of a session, what can be found in it, how far
it says it has read, and what it never holds."""

import json
import os
import sqlite3
import stat
import subprocess
import sys

import pytest
from sessions import ATTACKER, HOME, attack, benign
from standins import word_embedder

from summarizer import cli, hook
from summarizer.daemon import Daemon
from summarizer.embedder import HashEmbedder
from summarizer.index import COLUMNS, Index, IndexError_, behind, path, rows
from summarizer.store import EVENTS, SUMMARY, list_sessions, load_summary, session_dir
from summarizer.summarize import Runtime, Session

KEY = ("claude_code", "a")
CREDENTIALS = f"{HOME}/.aws/credentials"


def send(events, agent="claude_code"):
    for event in events:
        hook.handle(agent, json.dumps(event).encode())


def quiet(message):
    pass


def daemon(index=True):
    return Daemon(Runtime(HashEmbedder()), log=quiet, index=index)


def dump(index):
    """Every row of an index but the ids SQLite gave them, in a fixed order."""
    units = index.db.execute(
        f"SELECT s.agent, s.name, {', '.join('u.' + column for column in COLUMNS)} FROM units u"
        " JOIN sessions s ON s.id = u.session ORDER BY s.agent, s.name, u.unit"
    ).fetchall()
    names = index.db.execute(
        "SELECT s.agent, s.name, u.unit, n.kind, n.value FROM names n JOIN units u ON u.id = n.unit_id"
        " JOIN sessions s ON s.id = u.session ORDER BY s.agent, s.name, u.unit, n.kind, n.value"
    ).fetchall()
    sessions = index.db.execute(
        "SELECT agent, name, session_id, cwd, first_ts, last_ts, log_bytes, events, units,"
        " names_dropped FROM sessions ORDER BY agent, name"
    ).fetchall()
    return units, names, sessions


def test_the_daemon_writes_every_piece_of_a_session_and_what_it_names(home):
    send(attack(session_id="a"))
    served = daemon()
    assert served.update(*KEY) > 0
    session = served.sessions[KEY]
    log = session_dir(*KEY) / EVENTS
    with Index() as index:
        told = index.status()
        assert (told["sessions"], told["units"]) == (1, len(session.units)) and told["names"] > 0
        assert index.covered(*KEY) == os.path.getsize(log)
        assert dump(index)[0] == [
            {"agent": "claude_code", "name": "a"} | {column: row[column] for column in COLUMNS}
            for row in rows(session)
        ]
        # A name is found exactly as it was named: who named it, in which call or content, when.
        hits = index.naming(ATTACKER)
        assert hits and [hit["ts"] for hit in hits] == sorted(hit["ts"] for hit in hits)
        assert {hit["trust"] for hit in hits} >= {"untrusted", "agent"}
        upload = next(hit for hit in hits if hit["kind"] == "action")
        assert (
            upload["tool"] == "Bash"
            and upload["status"] == "returned"
            and ATTACKER in upload["text"]
        )
        assert all(hit["name"] == "a" and hit["agent"] == "claude_code" for hit in hits)
        assert any(hit["kind"] == "action" for hit in index.naming(CREDENTIALS, kind="path"))
        assert (
            index.naming(ATTACKER, kind="no-such-kind") == []
            and index.naming("nowhere.example") == []
        )
    # The file is the user's alone, as the logs are.
    assert stat.S_IMODE(os.stat(path()).st_mode) == 0o600


def test_indexing_changes_nothing_of_a_summary(home, monkeypatch):
    send(attack(session_id="a"))
    monkeypatch.setattr("time.time", lambda: 1_800_000_000.0)
    Daemon(Runtime(HashEmbedder()), log=quiet).update(*KEY)
    without = (session_dir(*KEY) / SUMMARY).read_bytes()
    assert not path().exists()  # only a daemon asked to index makes the file
    daemon().update(*KEY)
    assert (session_dir(*KEY) / SUMMARY).read_bytes() == without and path().exists()


def test_the_index_holds_nothing_a_model_said(home):
    send(attack(session_id="a"))
    daemon().update(*KEY)
    with Index() as index:
        columns = {
            row["name"]
            for table in ("sessions", "units", "names")
            for row in index.db.execute(f"PRAGMA table_info({table})").fetchall()
        }
    said_by_a_model = {
        "level",
        "labels",
        "label",
        "confidence",
        "indicators",
        "score",
        "vector",
        "vectors",
    }
    assert not columns & said_by_a_model
    # So two encoders give the same rows, and reading the logs again needs no model.
    records = [hook.build_record("claude_code", event) for event in attack(session_id="a")]
    made = []
    words = word_embedder()
    words.dim = words.info["dim"]  # the stand-in is made without what loads a model
    for embedder in (HashEmbedder(), words):
        session = Session("claude_code", "a", Runtime(embedder))
        for record in records:
            session.add(record)
        session.flush()
        made.append(rows(session))
    assert made[0] == made[1] and made[0]


def test_reading_event_by_event_and_reading_the_logs_again_give_the_same_rows(home, capsys):
    served, events = daemon(), attack(session_id="a")
    for event in events:  # as a live session arrives: a call is proposed, then it returns
        send([event])
        served.update(*KEY)
    send(benign(session_id="b"))
    served.update("claude_code", "b")
    with Index() as index:
        live = dump(index)
        statuses = {row["status"] for row in live[0] if row["kind"] == "action"}
    assert "proposed" not in statuses and "returned" in statuses
    served.index.close()
    assert cli.main(["index", "rebuild"]) == 0
    told = json.loads(capsys.readouterr().out)
    assert (told["rebuilt"], told["forgotten"], told["sessions"]) == (2, 0, 2)
    with Index() as index:
        assert dump(index) == live


def test_only_what_was_logged_before_a_time_is_found(home):
    send(attack(session_id="a"))
    daemon().update(*KEY)
    with Index() as index:
        hits = index.naming(ATTACKER)
        first, last = hits[0]["ts"], hits[-1]["ts"]
        assert index.naming(ATTACKER, before=first) == []
        assert index.naming(ATTACKER, before=last + 1) == hits
        assert [row["name"] for row in index.sessions()] == ["a"]
        assert index.sessions(before=first - 10_000) == [] and index.sessions(cwd="/nowhere") == []


def test_words_are_found_as_a_phrase_and_are_never_read_as_operators(home):
    send(attack(session_id="a"))
    daemon().update(*KEY)
    with Index() as index:
        if not index.words:
            with pytest.raises(IndexError_, match="full-text search is not in"):
                index.holding("anything")
            return
        hits = index.holding("Sync fails behind proxy")
        assert hits and all("Sync fails behind proxy" in hit["text"] for hit in hits)
        assert index.holding("fails Sync proxy behind") == []  # a phrase, in its order
        assert index.holding("Sync fails behind proxy", before=hits[0]["ts"]) == []
        for text in ('" OR text:*', "NEAR(a b)", "a AND NOT b", "*", '"', "text : x", ""):
            assert isinstance(index.holding(text), list)  # read as words, or as nothing


def test_a_session_the_index_has_not_read_to_its_end_says_so(home, capsys):
    assert behind(None, list_sessions()) == {"missing": [], "behind": [], "ahead": []}
    events = attack(session_id="a")
    send(events[:4])
    served = daemon()
    served.update(*KEY)
    send(events[4:])  # the log moves on and nobody has read it
    send(benign(session_id="b"))  # and a session nobody has read at all
    with Index() as index:
        found = behind(index, list_sessions())
        assert found == {"missing": [("claude_code", "b")], "behind": [KEY], "ahead": []}
    assert set(behind(None, list_sessions())["missing"]) == {KEY, ("claude_code", "b")}
    assert cli.main(["index", "status"]) == 0
    told = json.loads(capsys.readouterr().out)
    assert (told["logged_sessions"], told["sessions_missing"], told["sessions_behind"]) == (2, 1, 1)
    served.update(*KEY)
    served.update("claude_code", "b")
    with Index() as index:
        assert behind(index, list_sessions()) == {"missing": [], "behind": [], "ahead": []}


def test_a_log_that_was_replaced_leaves_nothing_of_the_old_one(home):
    send(attack(session_id="a"))
    served = daemon()
    served.update(*KEY)
    with Index() as index:
        assert index.naming(ATTACKER)
    os.unlink(session_dir(*KEY) / EVENTS)
    send(benign(session_id="a")[:3])  # a shorter log under the same name
    served.update(*KEY)
    with Index() as index:
        assert index.naming(ATTACKER) == [] and index.holding("Sync fails behind proxy") == []
        assert index.status()["units"] == len(served.sessions[KEY].units)
        assert index.covered(*KEY) == os.path.getsize(session_dir(*KEY) / EVENTS)


def test_a_failing_index_never_stops_a_summary(home, monkeypatch):
    said, calls = [], []

    def broken(*args, **kwargs):
        calls.append(args)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("summarizer.daemon.index_session", broken)
    served = Daemon(Runtime(HashEmbedder()), log=said.append, index=True)
    events = attack(session_id="a")
    send(events[:4])
    assert served.update(*KEY) == 4
    assert load_summary(*KEY)["fresh"] and served.index is False
    assert sum("search index off: OperationalError" in line for line in said) == 1
    send(events[4:])
    assert served.update(*KEY) == len(events) - 4 and load_summary(*KEY)["fresh"]
    assert len(calls) == 1  # it is not tried again, and the index says how far it got
    with Index() as index:
        assert behind(index, list_sessions())["missing"] == [KEY]


def test_prune_forgets_what_it_removes(home, capsys):
    served = daemon()
    for name, events in (("a", attack(session_id="a")), ("b", benign(session_id="b"))):
        send(events)
        served.update("claude_code", name)
    served.index.close()
    old = session_dir(*KEY) / EVENTS
    os.utime(old, (1_000_000_000, 1_000_000_000))
    assert cli.main(["prune", "--days", "1"]) == 0
    assert "removed claude_code/a" in capsys.readouterr().out
    with Index() as index:
        assert [row["name"] for row in index.sessions()] == ["b"]
        assert index.naming(ATTACKER) == [] and index.status()["sessions"] == 1


def test_the_daemon_is_the_one_writer_even_when_a_prune_runs_beside_it(home, capsys, monkeypatch):
    served = daemon()
    for name, events in (("a", attack(session_id="a")), ("b", benign(session_id="b"))):
        send(events)
        served.update("claude_code", name)
    served.index.close()
    os.utime(session_dir(*KEY) / EVENTS, (1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(cli, "_daemon_running", lambda: True)
    assert cli.main(["prune", "--days", "1"]) == 0
    assert "forgets them when the daemon next starts" in capsys.readouterr().err
    with Index() as index:  # the prune wrote nothing: a second writer never opened
        assert [row["name"] for row in index.sessions()] == ["a", "b"]
    restarted = daemon()
    restarted.update("claude_code", "b")
    with Index() as index:
        assert [row["name"] for row in index.sessions()] == ["b"] and index.naming(ATTACKER) == []


def test_an_older_sqlite_gets_a_rollback_journal(home, monkeypatch):
    with Index(write=True) as index:
        assert index.db.execute("PRAGMA journal_mode").fetchone()["journal_mode"] == "wal"
    os.unlink(path())
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 45, 1))
    with Index(write=True) as index:
        assert index.db.execute("PRAGMA journal_mode").fetchone()["journal_mode"] == "delete"


def test_a_column_holds_only_what_it_is_declared_to_hold(home):
    with Index(write=True) as index:
        with pytest.raises(sqlite3.IntegrityError):
            index.db.execute(
                "INSERT INTO sessions (agent, name, session_id, log_bytes, events, units)"
                " VALUES ('a', 'b', 'c', 'not a number', 0, 0)"
            )


def test_search_prints_hits_as_lines_and_says_when_there_is_no_index(home, capsys):
    with pytest.raises(SystemExit, match="no index yet"):
        cli.main(["search", "--name", ATTACKER])
    send(attack(session_id="a"))
    daemon().update(*KEY)
    assert cli.main(["search", "--name", ATTACKER, "--limit", "2"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 2 and all(
        line["name"] == "a" and len(line["text"]) <= 200 for line in lines
    )
    assert cli.main(["search", "--text", "upside down"]) == 0
    assert "upside down" in capsys.readouterr().out


def test_another_schema_is_refused_and_a_reader_cannot_write(home):
    send(attack(session_id="a"))
    daemon().update(*KEY)
    with Index() as index:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            index.db.execute("DELETE FROM units")
    raw = sqlite3.connect(path())
    raw.execute("UPDATE meta SET value = '99' WHERE key = 'schema'")
    raw.commit()
    raw.close()
    with pytest.raises(IndexError_, match="has schema 99"):
        Index()
    with pytest.raises(FileNotFoundError):
        Index(path().with_name("absent.sqlite"))


def test_the_hook_never_opens_the_index():
    code = "import sys, summarizer.hook; print('sqlite3' in sys.modules, 'summarizer.index' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["False", "False"]
