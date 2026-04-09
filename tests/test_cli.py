"""Tests for cclog.cli — calls CLI functions directly."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import cclog.cli as cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs):
    """Build a minimal argparse.Namespace with given attributes."""
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _capture_console(monkeypatch) -> StringIO:
    """Redirect cli._console to an in-memory StringIO and return it."""
    buf = StringIO()
    monkeypatch.setattr(cli, "_console", Console(file=buf, highlight=False, markup=False))
    return buf


# ---------------------------------------------------------------------------
# test_cmd_today_no_data
# ---------------------------------------------------------------------------

def test_cmd_today_no_data(tmp_path, monkeypatch):
    db_file = tmp_path / "audit.db"
    # Create empty schema
    from cclog.db import get_db
    conn = get_db(str(db_file))
    conn.close()

    monkeypatch.setenv("CCLOG_DB", str(db_file))
    buf = _capture_console(monkeypatch)

    cli.cmd_today(_make_args())

    output = buf.getvalue()
    assert "No sessions logged today" in output


# ---------------------------------------------------------------------------
# test_cmd_sessions_no_data
# ---------------------------------------------------------------------------

def test_cmd_sessions_no_data(tmp_path, monkeypatch):
    db_file = tmp_path / "audit.db"
    from cclog.db import get_db
    conn = get_db(str(db_file))
    conn.close()

    monkeypatch.setenv("CCLOG_DB", str(db_file))
    buf = _capture_console(monkeypatch)

    # Should not raise
    cli.cmd_sessions(_make_args(**{"all": False}))

    output = buf.getvalue()
    assert "No sessions" in output


# ---------------------------------------------------------------------------
# test_cmd_query_valid_sql
# ---------------------------------------------------------------------------

def test_cmd_query_valid_sql(tmp_path, monkeypatch):
    db_file = tmp_path / "audit.db"
    from cclog.db import get_db
    conn = get_db(str(db_file))
    conn.close()

    monkeypatch.setenv("CCLOG_DB", str(db_file))
    buf = _capture_console(monkeypatch)

    cli.cmd_query(_make_args(sql="SELECT 1 AS val"))

    output = buf.getvalue()
    assert "1" in output


# ---------------------------------------------------------------------------
# test_cmd_query_invalid_sql
# ---------------------------------------------------------------------------

def test_cmd_query_invalid_sql(tmp_path, monkeypatch):
    db_file = tmp_path / "audit.db"
    from cclog.db import get_db
    conn = get_db(str(db_file))
    conn.close()

    monkeypatch.setenv("CCLOG_DB", str(db_file))
    _capture_console(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_query(_make_args(sql="SELECT FROM"))

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# test_is_daemon_running_no_pid
# ---------------------------------------------------------------------------

def test_is_daemon_running_no_pid(tmp_path, monkeypatch):
    # Point _pid_path to a non-existent file
    fake_pid = tmp_path / "daemon.pid"
    monkeypatch.setattr(cli, "_pid_path", lambda: fake_pid)

    assert cli._is_daemon_running() is False


# ---------------------------------------------------------------------------
# test_parse_datetime_arg_*
# ---------------------------------------------------------------------------

def test_parse_datetime_arg_date_only():
    from cclog.cli import _parse_datetime_arg
    ms = _parse_datetime_arg("2026-04-09")
    assert ms == int(datetime(2026, 4, 9, 0, 0, 0).timestamp() * 1000)

def test_parse_datetime_arg_datetime():
    from cclog.cli import _parse_datetime_arg
    ms = _parse_datetime_arg("2026-04-09 14:30")
    assert ms == int(datetime(2026, 4, 9, 14, 30, 0).timestamp() * 1000)

def test_parse_datetime_arg_invalid():
    import argparse
    from cclog.cli import _parse_datetime_arg
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_datetime_arg("not-a-date")

def test_cmd_sessions_from_filter(tmp_path, monkeypatch):
    import sqlite3, argparse
    from cclog.db import setup_schema
    from cclog.cli import cmd_sessions
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    setup_schema(conn)
    # Insert session with last_active in the past
    past_ms = int(datetime(2026, 1, 1).timestamp() * 1000)
    conn.execute("INSERT INTO sessions (id, started_at, cwd) VALUES ('old-sess', ?, '/old')", (past_ms,))
    conn.commit(); conn.close()
    monkeypatch.setenv("CCLOG_DB", str(db))
    args = argparse.Namespace(all=True, from_dt=int(datetime(2026, 3, 1).timestamp()*1000), to_dt=None)
    # Should not crash; old-sess is before from_dt so output should be empty
    cmd_sessions(args)
