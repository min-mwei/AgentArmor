"""A search index over the sessions the hooks have logged. Standard library only.

    $SUMMARIZER_HOME/index.sqlite     derived; written by the daemon alone; safe to delete

The event logs are the source of truth, and a summary is what a detector reads. This file
is neither. It is a way to find things in them: which sessions named a path, a host or an
identifier, and when; and which pieces of text hold some words. Whoever needs evidence
opens the session it points to.

Three rules keep it a help and never an authority:

- **It holds what the log says, never what a model said of it.** A row is a piece of a
  session as the summarizer cut it: when, who (the user, the agent, lower-trust content),
  where from, the tool, what became of a call, the text, and the paths, hosts and
  identifiers named in it. There is no level, no label, no score and no vector here, so
  nothing goes stale when a model or its probes are replaced, and the index can be rebuilt
  on a machine that has no model.
- **It says how far it has read.** Every session has the bytes of its log the index covers.
  A session whose log has moved on, or that is not here at all, is said to be behind or
  missing by behind() and status(): nothing found in such a session is not nothing there.
- **A hit is a candidate.** A name or a word found here ties nothing to anything. It says
  where to look, in a session and before a time. The ties a packet shows are made by
  summarizer.history from the summaries, as they stood when a call was proposed.

The hook never opens this file. It appends to a session's own log and returns: many hooks
writing one database would have to wait for each other, and a hook must not wait. The
daemon writes here after it has replaced a summary, one transaction for each update, and a
failure here never stops it summarizing. Readers open the file read-only.

Agent messages (what an agent said when it stopped) are not indexed yet: prompts, calls
and what calls returned are.
"""

import json
import os
import sqlite3
from pathlib import Path

from .store import EVENTS, home, private_dir

NAME = "index.sqlite"
SCHEMA = 1
COLUMNS = (
    "unit",
    "seq",
    "ts",
    "kind",
    "trust",
    "source",
    "origin",
    "tool",
    "call_id",
    "status",
    "text",
)
_TABLES = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    name TEXT NOT NULL,                 -- the session's folder under sessions/<agent>/
    session_id TEXT NOT NULL,
    cwd TEXT NOT NULL DEFAULT '',
    first_ts REAL,
    last_ts REAL,
    log_bytes INTEGER NOT NULL,         -- how much of events.jsonl these rows cover
    events INTEGER NOT NULL,
    units INTEGER NOT NULL,
    names_dropped INTEGER NOT NULL DEFAULT 0,  -- names the summarizer had no room to track
    UNIQUE (agent, name)
);
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    session INTEGER NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    unit INTEGER NOT NULL,              -- its place among the session's pieces, from 0
    seq INTEGER NOT NULL,               -- the hook event it came from, from 1
    ts REAL NOT NULL,
    kind TEXT NOT NULL,                 -- prompt | action | output
    trust TEXT NOT NULL,                -- user | agent | untrusted | derived
    source TEXT NOT NULL,
    origin TEXT NOT NULL,
    tool TEXT NOT NULL,
    call_id TEXT NOT NULL,
    status TEXT NOT NULL,               -- of a call: proposed | returned | failed | denied
    text TEXT NOT NULL,
    UNIQUE (session, unit)
);
CREATE INDEX IF NOT EXISTS units_by_time ON units (ts);
CREATE TABLE IF NOT EXISTS names (
    kind TEXT NOT NULL,                 -- path | host | ... as the summarizer names them
    value TEXT NOT NULL,
    unit_id INTEGER NOT NULL REFERENCES units (id) ON DELETE CASCADE,
    PRIMARY KEY (kind, value, unit_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS names_by_unit ON names (unit_id);
"""
_WORDS = "CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(text, tokenize = 'unicode61')"


class IndexError_(RuntimeError):
    """The index cannot be used as it is: another schema, or not an index at all."""


def path():
    return home() / NAME


def _row(cursor, row):
    return {column[0]: value for column, value in zip(cursor.description, row, strict=True)}


class Index:
    """The index of one spool. Open it to read with Index(); the daemon opens Index(write=True)."""

    def __init__(self, file=None, write=False):
        self.file = Path(file) if file is not None else path()
        self.write = write
        if write:
            private_dir(self.file.parent)
            # Made private before SQLite opens it: it holds what the session logs hold.
            os.close(os.open(self.file, os.O_RDWR | os.O_CREAT, 0o600))
            self.db = sqlite3.connect(self.file, timeout=5.0, isolation_level=None)
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = NORMAL")
        else:
            if not self.file.is_file():
                raise FileNotFoundError(self.file)
            self.db = sqlite3.connect(f"file:{self.file}?mode=ro", uri=True, timeout=5.0)
        self.db.row_factory = _row
        self.db.execute("PRAGMA foreign_keys = ON")
        if write:
            self._create()
        found = self._meta("schema")
        if found != str(SCHEMA):
            self.db.close()
            raise IndexError_(f"{self.file.name} has schema {found}, this version reads {SCHEMA}")
        self.words = self._meta("words") == "fts5"

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _meta(self, key):
        try:
            row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        except sqlite3.DatabaseError:
            return None
        return row["value"] if row else None

    def _create(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.executescript(_TABLES) if not self._has("meta") else None
        if self._meta("schema") is None:
            try:
                self.db.execute(_WORDS)
                words = "fts5"
            except sqlite3.OperationalError:  # this Python's SQLite was built without FTS5
                words = "none"
            with self.db:
                self.db.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
                self.db.execute("INSERT INTO meta VALUES ('words', ?)", (words,))

    def _has(self, table):
        row = self.db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone()
        return row is not None

    # --- writing: the daemon only ---------------------------------------------------------------

    def record(
        self, agent, name, session_id, units, log_bytes, events, total, cwd="", names_dropped=0
    ):
        """Bring one session's rows up to what the summarizer holds, in one transaction.

        units are pieces of the session as dicts with COLUMNS and "names", a list of
        (kind, value). A piece whose "unit" is here already is replaced, which is how a call's
        status follows what became of it; its text never changes. When the pieces start at 0
        the session is written afresh, so a log that was replaced leaves nothing behind.
        total is how many pieces the session has, log_bytes how much of its log they cover.
        """
        units = list(units)
        fresh = bool(units) and min(unit["unit"] for unit in units) == 0
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT id FROM sessions WHERE agent = ? AND name = ?", (agent, name)
            ).fetchone()
            if row is not None and fresh:
                self._drop(row["id"])
                row = None
            if row is None:
                made = self.db.execute(
                    "INSERT INTO sessions (agent, name, session_id, log_bytes, events, units)"
                    " VALUES (?, ?, ?, 0, 0, 0)",
                    (agent, name, str(session_id)),
                )
                session = made.lastrowid
            else:
                session = row["id"]
            for unit in units:
                values = [unit[column] for column in COLUMNS]
                old = self.db.execute(
                    "SELECT id FROM units WHERE session = ? AND unit = ?", (session, unit["unit"])
                ).fetchone()
                if old is not None:
                    self.db.execute(
                        "UPDATE units SET status = ? WHERE id = ?", (unit["status"], old["id"])
                    )
                    continue
                made = self.db.execute(
                    f"INSERT INTO units (session, {', '.join(COLUMNS)})"
                    f" VALUES (?{', ?' * len(COLUMNS)})",
                    (session, *values),
                )
                for kind, value in sorted(set(map(tuple, unit.get("names", ())))):
                    self.db.execute(
                        "INSERT OR IGNORE INTO names VALUES (?, ?, ?)",
                        (kind, value, made.lastrowid),
                    )
                if self.words:
                    self.db.execute(
                        "INSERT INTO words (rowid, text) VALUES (?, ?)",
                        (made.lastrowid, unit["text"]),
                    )
            times = self.db.execute(
                "SELECT min(ts) AS first, max(ts) AS last FROM units WHERE session = ?", (session,)
            ).fetchone()
            self.db.execute(
                "UPDATE sessions SET session_id = ?, cwd = ?, first_ts = ?, last_ts = ?,"
                " log_bytes = ?, events = ?, units = ?, names_dropped = ? WHERE id = ?",
                (
                    str(session_id),
                    cwd or "",
                    times["first"],
                    times["last"],
                    log_bytes,
                    events,
                    total,
                    names_dropped,
                    session,
                ),
            )
        return session

    def _drop(self, session):
        if self.words:
            self.db.execute(
                "DELETE FROM words WHERE rowid IN (SELECT id FROM units WHERE session = ?)",
                (session,),
            )
        self.db.execute("DELETE FROM sessions WHERE id = ?", (session,))

    def forget(self, agent, name):
        """Remove a session whose log is gone. Returns whether it was here."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT id FROM sessions WHERE agent = ? AND name = ?", (agent, name)
            ).fetchone()
            if row is not None:
                self._drop(row["id"])
        return row is not None

    # --- reading ----------------------------------------------------------------------------------

    def sessions(self, cwd=None, before=None):
        """The sessions here, oldest first: optionally those of one working directory, and
        those that began before a time."""
        query, given = "SELECT * FROM sessions WHERE 1", []
        if cwd is not None:
            query, given = query + " AND cwd = ?", [*given, cwd]
        if before is not None:
            query, given = query + " AND first_ts < ?", [*given, before]
        return self.db.execute(query + " ORDER BY first_ts, id", given).fetchall()

    def covered(self, agent, name):
        """How many bytes of a session's log its rows cover, or None when it is not here."""
        row = self.db.execute(
            "SELECT log_bytes FROM sessions WHERE agent = ? AND name = ?", (agent, name)
        ).fetchone()
        return row["log_bytes"] if row else None

    _FOUND = (
        "SELECT s.agent, s.name, s.session_id, s.cwd, u.unit, u.seq, u.ts, u.kind, u.trust,"
        " u.source, u.origin, u.tool, u.call_id, u.status, u.text"
    )

    def naming(self, value, kind=None, before=None, limit=200):
        """Pieces that name a path, a host or an identifier exactly, oldest first. before is a
        time: only what was logged earlier. A hit says where to look and ties nothing."""
        query = self._FOUND + ", n.kind AS named_as FROM names n JOIN units u ON u.id = n.unit_id"
        query += " JOIN sessions s ON s.id = u.session WHERE n.value = ?"
        given = [value]
        if kind is not None:
            query, given = query + " AND n.kind = ?", [*given, kind]
        if before is not None:
            query, given = query + " AND u.ts < ?", [*given, before]
        return self.db.execute(query + " ORDER BY u.ts, u.id LIMIT ?", (*given, limit)).fetchall()

    def holding(self, words, before=None, limit=50):
        """Pieces whose text holds these words, best match first: full-text search, where
        this Python's SQLite has it. words is one phrase: it is quoted whole, so nothing in
        it is read as a search operator."""
        if not self.words:
            raise IndexError_("full-text search is not in this Python's SQLite")
        phrase = '"' + str(words).replace('"', '""') + '"'
        query = (
            self._FOUND
            + " FROM words w JOIN units u ON u.id = w.rowid JOIN sessions s ON s.id = u.session"
        )
        query += " WHERE words MATCH ?"
        given = [phrase]
        if before is not None:
            query, given = query + " AND u.ts < ?", [*given, before]
        return self.db.execute(query + " ORDER BY rank, u.ts LIMIT ?", (*given, limit)).fetchall()

    def status(self):
        """Counts, and whether full-text search is here. No text."""
        told = {"file": str(self.file), "schema": SCHEMA, "full_text": self.words}
        for table in ("sessions", "units", "names"):
            told[table] = self.db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        return told


def rows(session, start=0, also=()):
    """What the log says of a summarizer session's pieces from `start` on, and of the pieces
    listed in also: rows for record(). session is a summarize.Session, read by its plain
    fields: when, who, where from, the tool, what became of a call, the text and the names
    in it. Nothing it embedded and nothing it marked is taken."""
    units = session.units
    chosen = sorted(
        {index for index in also if 0 <= index < len(units)} | set(range(start, len(units)))
    )
    made = []
    for index in chosen:
        unit = units[index]
        row = {"unit": index, "seq": unit.seq, "ts": float(unit.ts or 0.0), "kind": unit.kind}
        row |= {
            "trust": unit.trust,
            "source": unit.source,
            "origin": unit.origin,
            "tool": unit.tool,
        }
        row |= {"call_id": unit.call_id, "status": unit.status, "text": unit.text}
        made.append(row | {"names": sorted(set(unit.iocs))})
    return made


def record(index, agent, name, session, start=0, also=()):
    """Write a summarizer session's pieces from `start` on to an index. Returns the rows."""
    units = rows(session, start, also)
    index.record(
        agent,
        name,
        session.session_id,
        units,
        session.log_bytes,
        session.events,
        len(session.units),
        cwd=str(session.info.get("cwd") or ""),
        names_dropped=session.counts["observables_dropped"],
    )
    return units


def behind(index, sessions):
    """Of (agent, name, directory, ...) sessions, as store.list_sessions gives them: those
    the index does not cover to the end of their log. "missing": not here at all;
    "behind": the log has moved on; "ahead": the log is shorter than what was read, so it was
    replaced. An index that is absent covers nothing."""
    found = {"missing": [], "behind": [], "ahead": []}
    for agent, name, directory, *_ in sessions:
        try:
            size = os.path.getsize(Path(directory) / EVENTS)
        except OSError:
            continue
        covered = index.covered(agent, name) if index is not None else None
        if covered is None:
            found["missing"].append((agent, name))
        elif covered < size:
            found["behind"].append((agent, name))
        elif covered > size:
            found["ahead"].append((agent, name))
    return found


def shown(row, chars=200):
    """One hit as a line of JSON, its text cut for a terminal."""
    text = row["text"] if len(row["text"]) <= chars else row["text"][: chars - 1] + "…"
    return json.dumps(row | {"text": text}, ensure_ascii=False)
