"""A search index over the sessions the hooks have logged. Standard library only.

    $SUMMARIZER_HOME/index.sqlite     derived; written by the daemon alone; safe to delete

The event logs are the source of truth, and a summary is what a detector reads. This file
is neither. It is a way to find things in them: which sessions named a path, a host or an
identifier, and when; and which pieces of text hold some words. Whoever needs evidence
opens the session it points to.

Three rules keep it a help and never an authority:

- **What the log says and what a model said of it are kept apart.** A piece is a row as the
  summarizer cut it: when, who (the user, the agent, lower-trust content), where from, the
  tool, what became of a call, the text, and the paths, hosts and identifiers named in it.
  Those tables hold no level, no label and no score, so they do not go stale when a model
  or its probes are replaced, and they can be rebuilt on a machine that has no model. The
  one thing kept of a model is each piece's vector, in a table of its own, under the name
  of the encoder that made it. Vectors of two encoders are never compared, and a rebuild
  from the logs leaves them where they are.
- **It says how far it has read.** Every session has the bytes of its log the index covers.
  A session whose log has moved on, or that is not here at all, is said to be behind or
  missing by behind() and status(): nothing found in such a session is not nothing there.
- **A hit is a candidate.** A name, a phrase or a likeness found here ties nothing to
  anything. It says where to look, in a session and before a time. The ties a packet shows
  are made by summarizer.history from the summaries, as they stood when a call was proposed.

The hook never opens this file. It appends to a session's own log and returns: many hooks
writing one database would have to wait for each other, and a hook must not wait. The
daemon writes here after it has replaced a summary, one transaction for each update, and a
failure here never stops it summarizing. Readers open the file read-only.

Agent messages (what an agent said when it stopped) are not indexed yet: prompts, calls
and what calls returned are. Everything here is the standard library but like(), the
search by vector, which needs NumPy and says so when it is missing.
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
) STRICT;
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
) STRICT;
CREATE INDEX IF NOT EXISTS units_by_time ON units (ts);
CREATE TABLE IF NOT EXISTS names (
    kind TEXT NOT NULL,                 -- path | host | ... as the summarizer names them
    value TEXT NOT NULL,
    unit_id INTEGER NOT NULL REFERENCES units (id) ON DELETE CASCADE,
    PRIMARY KEY (kind, value, unit_id)
) WITHOUT ROWID, STRICT;
CREATE INDEX IF NOT EXISTS names_by_unit ON names (unit_id);
CREATE TABLE IF NOT EXISTS encoders (
    id INTEGER PRIMARY KEY,
    identity TEXT NOT NULL UNIQUE,      -- what made the vectors, as the runtime names itself
    dim INTEGER NOT NULL                -- how many numbers of each vector are kept
) STRICT;
CREATE TABLE IF NOT EXISTS vectors (
    encoder INTEGER NOT NULL REFERENCES encoders (id) ON DELETE CASCADE,
    unit_id INTEGER NOT NULL REFERENCES units (id) ON DELETE CASCADE,
    vec BLOB NOT NULL,                  -- dim float32 numbers, little-endian, of length one
    PRIMARY KEY (encoder, unit_id)
) WITHOUT ROWID, STRICT;
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
            # A write-ahead log lets readers go on while the daemon writes. Before SQLite
            # 3.51.3 such a log could be corrupted when two connections wrote or
            # checkpointed at the same instant. There is one writer here; an older SQLite
            # gets a rollback journal all the same, and readers then wait their turn.
            mode = "WAL" if sqlite3.sqlite_version_info >= (3, 51, 3) else "DELETE"
            self.db.execute(f"PRAGMA journal_mode = {mode}")
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
        self._matrices = {}  # encoder -> what like() last read of its vectors

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
        self,
        agent,
        name,
        session_id,
        units,
        log_bytes,
        events,
        total,
        cwd="",
        names_dropped=0,
        encoder=None,
    ):
        """Bring one session's rows up to what the summarizer holds, in one transaction.

        units are pieces of the session as dicts with COLUMNS, "names" (a list of
        (kind, value)) and, where the piece was embedded, "vector" (float32 bytes).
        encoder is (identity, dimension) of what made those vectors. total is how many
        pieces the session has, log_bytes how much of its log they cover.

        A piece that is here already and unchanged stays the row it was, with its names
        and its vectors: only its status follows what became of a call. A piece that
        differs is a piece of another log under the same name: it is replaced, and what
        hung on the old one goes with it. Pieces beyond total are removed. So reading a
        log again changes nothing, and a log that was replaced leaves nothing behind.
        """
        fixed = [column for column in COLUMNS if column != "status"]
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT id FROM sessions WHERE agent = ? AND name = ?", (agent, name)
            ).fetchone()
            if row is None:
                made = self.db.execute(
                    "INSERT INTO sessions (agent, name, session_id, log_bytes, events, units)"
                    " VALUES (?, ?, ?, 0, 0, 0)",
                    (agent, name, str(session_id)),
                )
                session = made.lastrowid
            else:
                session = row["id"]
            made_by = self._encoder(encoder) if encoder is not None else None
            for unit in units:
                old = self.db.execute(
                    f"SELECT id, {', '.join(fixed)} FROM units WHERE session = ? AND unit = ?",
                    (session, unit["unit"]),
                ).fetchone()
                if old is not None and all(old[column] == unit[column] for column in fixed):
                    piece = old["id"]
                    self.db.execute(
                        "UPDATE units SET status = ? WHERE id = ?", (unit["status"], piece)
                    )
                else:
                    if old is not None:
                        self._remove("id = ?", (old["id"],))
                    made = self.db.execute(
                        f"INSERT INTO units (session, {', '.join(COLUMNS)})"
                        f" VALUES (?{', ?' * len(COLUMNS)})",
                        (session, *(unit[column] for column in COLUMNS)),
                    )
                    piece = made.lastrowid
                    for kind, value in sorted(set(map(tuple, unit.get("names", ())))):
                        self.db.execute(
                            "INSERT OR IGNORE INTO names VALUES (?, ?, ?)", (kind, value, piece)
                        )
                    if self.words:
                        self.db.execute(
                            "INSERT INTO words (rowid, text) VALUES (?, ?)", (piece, unit["text"])
                        )
                if made_by is not None and unit.get("vector") is not None:
                    self.db.execute(
                        "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?)",
                        (made_by, piece, unit["vector"]),
                    )
            self._remove("session = ? AND unit >= ?", (session, total))
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

    def _encoder(self, encoder):
        """The row of an encoder, made when it is first seen."""
        identity, dim = encoder
        row = self.db.execute("SELECT id, dim FROM encoders WHERE identity = ?", (identity,))
        row = row.fetchone()
        if row is None:
            made = self.db.execute("INSERT INTO encoders (identity, dim) VALUES (?, ?)", encoder)
            return made.lastrowid
        if row["dim"] != dim:
            raise IndexError_("an encoder's vectors are kept at one dimension")
        return row["id"]

    def _remove(self, where, given):
        """Delete pieces of the units table. Their names and vectors go with the rows; the
        full-text entries are not tied to them and are deleted here."""
        if self.words:
            self.db.execute(
                f"DELETE FROM words WHERE rowid IN (SELECT id FROM units WHERE {where})", given
            )
        self.db.execute(f"DELETE FROM units WHERE {where}", given)

    def _drop(self, session):
        self._remove("session = ?", (session,))
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

    def like(self, vector, encoder, before=None, limit=20):
        """Pieces whose vector is nearest to this one, nearest first, each with its "score"
        (the cosine). vector is a sequence of numbers at least as long as the encoder keeps;
        encoder is the identity the vectors were stored under, and only its vectors are
        searched: vectors of two encoders are of two spaces. An exact scan, which at the
        sizes of one machine's sessions is a few milliseconds.

        Likeness is the weakest lead there is. Text that says the opposite, or the same
        events in another order, can be as near as text that agrees."""
        try:
            import numpy as np
        except ImportError:
            raise IndexError_("the search by vector needs NumPy") from None
        row = self.db.execute("SELECT id, dim FROM encoders WHERE identity = ?", (encoder,))
        row = row.fetchone()
        if row is None:
            return []
        ids, times, matrix = self._matrix(np, row["id"], row["dim"])
        query = np.asarray(vector, dtype=np.float32).ravel()[: row["dim"]]
        size = float(np.linalg.norm(query))
        if not len(ids) or len(query) < row["dim"] or size == 0.0:
            return []
        scores = matrix @ (query / size)
        if before is not None:
            scores = np.where(times < before, scores, -np.inf)
        count = min(limit, int(np.isfinite(scores).sum()))
        if count <= 0:
            return []
        top = np.argpartition(scores, -count)[-count:]
        found = []
        for at in top[np.argsort(scores[top], kind="stable")[::-1]]:
            hit = self.db.execute(
                self._FOUND + " FROM units u JOIN sessions s ON s.id = u.session WHERE u.id = ?",
                (int(ids[at]),),
            ).fetchone()
            found.append(hit | {"score": float(scores[at])})
        return found

    def _matrix(self, np, encoder, dim):
        """(piece ids, their times, their vectors as one matrix) of an encoder, read again
        only when vectors were added or removed since the last look."""
        seen = self.db.execute(
            "SELECT count(*) AS n, max(unit_id) AS last FROM vectors WHERE encoder = ?", (encoder,)
        ).fetchone()
        held = self._matrices.get(encoder)
        if held is None or held[0] != (seen["n"], seen["last"]):
            rows = self.db.execute(
                "SELECT v.unit_id, u.ts, v.vec FROM vectors v JOIN units u ON u.id = v.unit_id"
                " WHERE v.encoder = ? ORDER BY v.unit_id",
                (encoder,),
            ).fetchall()
            if any(len(row["vec"]) != 4 * dim for row in rows):
                raise IndexError_("a stored vector is not of its encoder's dimension")
            ids = np.fromiter((row["unit_id"] for row in rows), np.int64, len(rows))
            times = np.fromiter((row["ts"] for row in rows), np.float64, len(rows))
            matrix = np.frombuffer(b"".join(row["vec"] for row in rows), dtype="<f4")
            held = self._matrices[encoder] = (
                (seen["n"], seen["last"]),
                ids,
                times,
                matrix.reshape(len(rows), dim),
            )
        return held[1:]

    def status(self):
        """Counts, and whether full-text search is here. No text."""
        told = {"file": str(self.file), "schema": SCHEMA, "full_text": self.words}
        for table in ("sessions", "units", "names", "vectors", "encoders"):
            told[table] = self.db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        return told


def rows(session, start=0, also=()):
    """What the log says of a summarizer session's pieces from `start` on, and of the pieces
    listed in also: rows for record(). session is a summarize.Session, read by its plain
    fields: when, who, where from, the tool, what became of a call, the text and the names
    in it. Nothing it marked is taken. A piece it embedded comes with its vector."""
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
        row |= {"names": sorted(set(unit.iocs)), "vector": None}
        if index < session.embedded and index not in session.unembedded:
            part = session.vectors[index][: encoder_of(session)[1]]
            size = float(part @ part) ** 0.5
            if size > 0.0:  # kept at the dimension the summaries keep, and of length one again
                row["vector"] = (part / size).astype("<f4").tobytes()
        made.append(row)
    return made


def encoder_of(session):
    """(identity, dimension kept) of what made a session's vectors: the encoder as the
    runtime names it, without what only concerns marking, and how much of a vector is kept."""
    made = session.runtime.identity()["embedding"]
    made = {key: value for key, value in made.items() if key not in ("probes", "calibrated")}
    kept = min(session.runtime.config.vector_dim, session.vectors.shape[1])
    return json.dumps(made | {"kept": kept}, sort_keys=True), kept


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
        encoder=encoder_of(session),
    )
    return units


def forget_missing(index, sessions):
    """Remove from an index the sessions whose logs are no longer in the spool. sessions is
    what store.list_sessions gives. Returns how many were removed."""
    logged = {(agent, name) for agent, name, *_ in sessions}
    gone = [(row["agent"], row["name"]) for row in index.sessions()]
    gone = [key for key in gone if key not in logged]
    for agent, name in gone:
        index.forget(agent, name)
    return len(gone)


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
