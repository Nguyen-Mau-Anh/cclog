"""Tests for cclog.db — schema creation and WAL-mode connection."""
import sqlite3
import pytest

from cclog.db import get_db, setup_schema


def test_setup_schema_creates_sessions_table():
    conn = sqlite3.connect(":memory:")
    setup_schema(conn)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    assert cur.fetchone() is not None
    conn.close()


def test_setup_schema_creates_events_table():
    conn = sqlite3.connect(":memory:")
    setup_schema(conn)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
    assert cur.fetchone() is not None
    conn.close()


def test_setup_schema_creates_token_ledger_table():
    conn = sqlite3.connect(":memory:")
    setup_schema(conn)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_ledger'")
    assert cur.fetchone() is not None
    conn.close()


def test_setup_schema_creates_indexes():
    conn = sqlite3.connect(":memory:")
    setup_schema(conn)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
        "('idx_events_session','idx_events_tool','idx_events_time')"
    )
    names = {row[0] for row in cur.fetchall()}
    assert names == {"idx_events_session", "idx_events_tool", "idx_events_time"}
    conn.close()


def test_setup_schema_is_idempotent():
    conn = sqlite3.connect(":memory:")
    setup_schema(conn)
    setup_schema(conn)
    conn.close()


def test_sessions_columns(mem_conn):
    mem_conn.execute(
        "INSERT INTO sessions (id, started_at, model, cwd) VALUES (?,?,?,?)",
        ("s1", 1000, "claude-opus-4", "/home/user"),
    )
    row = mem_conn.execute("SELECT * FROM sessions WHERE id='s1'").fetchone()
    assert row["id"] == "s1"
    assert row["started_at"] == 1000
    assert row["model"] == "claude-opus-4"
    assert row["cwd"] == "/home/user"


def test_events_columns(mem_conn):
    mem_conn.execute("INSERT INTO sessions (id, started_at) VALUES (?,?)", ("s1", 1000))
    mem_conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json, output_json) "
        "VALUES (?,?,?,?,?,?)",
        ("s1", "pre", "Bash", 2000, '{"command":"ls"}', None),
    )
    row = mem_conn.execute("SELECT * FROM events WHERE session_id='s1'").fetchone()
    assert row["phase"] == "pre"
    assert row["tool_name"] == "Bash"


def test_token_ledger_columns(mem_conn):
    mem_conn.execute("INSERT INTO sessions (id, started_at) VALUES (?,?)", ("s1", 1000))
    mem_conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at) VALUES (?,?,?,?)",
        ("s1", "post", "Read", 3000),
    )
    event_id = mem_conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    mem_conn.execute(
        "INSERT INTO token_ledger (event_id, gross_input, gross_output, net_input, cost_usd, counted_by, model) "
        "VALUES (?,?,?,?,?,?,?)",
        (event_id, 500, 100, 250, 0.003, "api", "claude-3-5-sonnet"),
    )
    row = mem_conn.execute("SELECT * FROM token_ledger WHERE event_id=?", (event_id,)).fetchone()
    assert row["gross_input"] == 500
    assert row["counted_by"] == "api"


def test_get_db_returns_connection(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_db(str(db_path))
    assert conn is not None
    conn.close()


def test_get_db_enables_wal(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_db(str(db_path))
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    conn.close()


def test_get_db_creates_schema(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_db(str(db_path))
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert {"sessions", "events", "token_ledger"}.issubset(tables)
    conn.close()


def test_get_db_creates_parent_dir(tmp_path):
    db_path = tmp_path / "nested" / "dir" / "audit.db"
    conn = get_db(str(db_path))
    assert db_path.exists()
    conn.close()
