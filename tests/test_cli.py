"""Tests for cclog.cli — calls CLI functions directly."""
from __future__ import annotations

import argparse
import sqlite3
import sys
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
