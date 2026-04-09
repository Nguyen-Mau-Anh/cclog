"""cclog.db — SQLite connection factory and schema management."""
from __future__ import annotations

import sqlite3
from pathlib import Path


_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    model       TEXT,
    cwd         TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT REFERENCES sessions(id),
    phase       TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    input_json  TEXT,
    output_json TEXT
);

CREATE TABLE IF NOT EXISTS token_ledger (
    event_id       INTEGER PRIMARY KEY REFERENCES events(id),
    gross_input    INTEGER DEFAULT 0,
    gross_output   INTEGER DEFAULT 0,
    net_input      INTEGER,
    cost_usd       REAL,
    counted_by     TEXT,
    model          TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_tool    ON events(tool_name);
CREATE INDEX IF NOT EXISTS idx_events_time    ON events(occurred_at);
"""


def setup_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes idempotently (IF NOT EXISTS)."""
    conn.executescript(_DDL)
    conn.commit()


def get_db(path: str) -> sqlite3.Connection:
    """Return a WAL-mode SQLite connection, creating the file and parent dirs as needed."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=0.5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    setup_schema(conn)
    return conn
