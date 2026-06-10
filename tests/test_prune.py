"""Tests for cclog.prune — two-tier retention (strip JSON, keep ledger)."""
from __future__ import annotations

import time

import pytest

from cclog.prune import measure_prunable, strip_old_event_json, vacuum

CUTOFF = 1_000_000  # ms


def _seed(conn, *, old_with_json=0, old_without_json=0, new_with_json=0):
    conn.execute(
        "INSERT INTO sessions (id, started_at, model, cwd) VALUES ('s1', 1, 'm', '/tmp')"
    )
    ts = 0

    def add(occurred_at, with_json):
        conn.execute(
            "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json, output_json) "
            "VALUES ('s1', 'post', 'Bash', ?, ?, ?)",
            (occurred_at, '{"in": "x"}' if with_json else None, '{"out": "y"}' if with_json else None),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO token_ledger (event_id, gross_input, gross_output, net_input, cost_usd, counted_by, model) "
            "VALUES (?, 100, 10, 100, 0.5, 'api', 'm')",
            (event_id,),
        )

    for _ in range(old_with_json):
        ts += 1
        add(CUTOFF - 1000 + ts % 500, True)
    for _ in range(old_without_json):
        add(CUTOFF - 2000, False)
    for _ in range(new_with_json):
        add(CUTOFF + 1000, True)
    conn.commit()


def test_strip_removes_old_json_keeps_new(mem_conn):
    _seed(mem_conn, old_with_json=3, new_with_json=2)
    result = strip_old_event_json(mem_conn, CUTOFF)
    assert result.events_stripped == 3
    assert result.bytes_freed == 3 * (len('{"in": "x"}') + len('{"out": "y"}'))

    stripped = mem_conn.execute(
        "SELECT COUNT(*) FROM events WHERE occurred_at < ? AND input_json IS NULL AND output_json IS NULL",
        (CUTOFF,),
    ).fetchone()[0]
    assert stripped == 3
    kept = mem_conn.execute(
        "SELECT COUNT(*) FROM events WHERE occurred_at >= ? AND input_json IS NOT NULL",
        (CUTOFF,),
    ).fetchone()[0]
    assert kept == 2


def test_strip_preserves_event_rows_and_ledger(mem_conn):
    _seed(mem_conn, old_with_json=4)
    strip_old_event_json(mem_conn, CUTOFF)
    assert mem_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 4
    assert mem_conn.execute("SELECT COUNT(*) FROM token_ledger").fetchone()[0] == 4
    # Cost history intact
    total = mem_conn.execute("SELECT SUM(cost_usd) FROM token_ledger").fetchone()[0]
    assert total == pytest.approx(2.0)


def test_strip_is_idempotent(mem_conn):
    _seed(mem_conn, old_with_json=2)
    assert strip_old_event_json(mem_conn, CUTOFF).events_stripped == 2
    again = strip_old_event_json(mem_conn, CUTOFF)
    assert again.events_stripped == 0
    assert again.bytes_freed == 0


def test_measure_prunable_does_not_modify(mem_conn):
    _seed(mem_conn, old_with_json=2)
    result = measure_prunable(mem_conn, CUTOFF)
    assert result.events_stripped == 2
    assert result.bytes_freed > 0
    still_there = mem_conn.execute(
        "SELECT COUNT(*) FROM events WHERE input_json IS NOT NULL"
    ).fetchone()[0]
    assert still_there == 2


def test_strip_nothing_when_no_old_events(mem_conn):
    _seed(mem_conn, new_with_json=3)
    result = strip_old_event_json(mem_conn, CUTOFF)
    assert result.events_stripped == 0


def test_vacuum_reclaims_file_space(tmp_path):
    from cclog.db import get_db

    db_path = str(tmp_path / "audit.db")
    conn = get_db(db_path)
    conn.execute("INSERT INTO sessions (id, started_at, model, cwd) VALUES ('s', 1, 'm', '/')")
    big = "x" * 500_000
    for i in range(10):
        conn.execute(
            "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json) "
            "VALUES ('s', 'pre', 'Bash', ?, ?)",
            (1000 + i, big),
        )
    conn.commit()
    strip_old_event_json(conn, 10_000)
    vacuum(conn)
    conn.close()

    import os
    # 5MB of blobs stripped + vacuumed → file should be well under 1MB
    assert os.path.getsize(db_path) < 1_000_000


# ---------------------------------------------------------------------------
# Daemon auto-prune
# ---------------------------------------------------------------------------

def test_daemon_prune_if_due(tmp_path, monkeypatch):
    from cclog.daemon import CclogDaemon
    from cclog.db import get_db

    db_path = str(tmp_path / "audit.db")
    conn = get_db(db_path)
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 40 * 86400 * 1000  # 40 days old
    conn.execute("INSERT INTO sessions (id, started_at, model, cwd) VALUES ('s', 1, 'm', '/')")
    conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json) "
        "VALUES ('s', 'pre', 'Bash', ?, '{\"old\": 1}')",
        (old_ms,),
    )
    conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json) "
        "VALUES ('s', 'pre', 'Bash', ?, '{\"new\": 1}')",
        (now_ms,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CCLOG_RETENTION_DAYS", "30")
    d = CclogDaemon(db_path=db_path, sock_path=str(tmp_path / "x.sock"), port=1)

    result = d._prune_if_due()
    assert result is not None
    assert result.events_stripped == 1

    # Second call within 24h: not due
    assert d._prune_if_due() is None

    conn = get_db(db_path)
    rows = conn.execute("SELECT occurred_at, input_json FROM events ORDER BY occurred_at").fetchall()
    conn.close()
    assert rows[0]["input_json"] is None       # old: stripped
    assert rows[1]["input_json"] is not None   # recent: kept


def test_daemon_prune_disabled_without_env(tmp_path, monkeypatch):
    from cclog.daemon import CclogDaemon

    monkeypatch.delenv("CCLOG_RETENTION_DAYS", raising=False)
    d = CclogDaemon(db_path=str(tmp_path / "a.db"), sock_path=str(tmp_path / "x.sock"), port=1)
    assert d._prune_if_due() is None


def test_daemon_prune_ignores_invalid_env(tmp_path, monkeypatch):
    from cclog.daemon import CclogDaemon

    d = CclogDaemon(db_path=str(tmp_path / "a.db"), sock_path=str(tmp_path / "x.sock"), port=1)
    for bad in ("abc", "0", "-3"):
        monkeypatch.setenv("CCLOG_RETENTION_DAYS", bad)
        assert d._prune_if_due() is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_daemon_prunes_again_after_interval(tmp_path, monkeypatch):
    from cclog.daemon import CclogDaemon, _PRUNE_INTERVAL_MS
    from cclog.db import get_db

    db_path = str(tmp_path / "audit.db")
    conn = get_db(db_path)
    now_ms = int(time.time() * 1000)
    conn.execute("INSERT INTO sessions (id, started_at, model, cwd) VALUES ('s', 1, 'm', '/')")
    conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json) "
        "VALUES ('s', 'pre', 'Bash', ?, '{\"old\": 1}')",
        (now_ms - 40 * 86400 * 1000,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CCLOG_RETENTION_DAYS", "30")
    d = CclogDaemon(db_path=db_path, sock_path=str(tmp_path / "x.sock"), port=1)

    assert d._prune_if_due(now_ms=now_ms).events_stripped == 1
    assert d._prune_if_due(now_ms=now_ms + 1000) is None  # within 24h: gated
    # 24h later: due again (idempotent — nothing left to strip)
    again = d._prune_if_due(now_ms=now_ms + _PRUNE_INTERVAL_MS + 1)
    assert again is not None
    assert again.events_stripped == 0


def test_cli_main_survives_invalid_retention_env(tmp_path, monkeypatch, capsys):
    """A typo in CCLOG_RETENTION_DAYS must not crash any CLI command —
    the --days default is resolved lazily and tolerantly."""
    import sys as _sys
    from cclog.cli import main

    monkeypatch.setenv("CCLOG_RETENTION_DAYS", "abc")
    monkeypatch.setenv("CCLOG_DB", str(tmp_path / "audit.db"))
    monkeypatch.setattr(_sys, "argv", ["cclog", "prune", "--dry-run"])
    main()  # must not raise; falls back to 30 days
    out = capsys.readouterr().out
    assert "Would strip" in out


def test_cli_prune_rejects_nonpositive_days(tmp_path, monkeypatch):
    import argparse
    from cclog.cli import cmd_prune

    monkeypatch.setenv("CCLOG_DB", str(tmp_path / "audit.db"))
    with pytest.raises(SystemExit):
        cmd_prune(argparse.Namespace(days=0, dry_run=False, no_vacuum=False))


def test_cli_prune_no_vacuum_flag(tmp_path, monkeypatch, capsys):
    import argparse
    from cclog.cli import cmd_prune
    from cclog.db import get_db

    db_path = str(tmp_path / "audit.db")
    conn = get_db(db_path)
    conn.execute("INSERT INTO sessions (id, started_at, model, cwd) VALUES ('s', 1, 'm', '/')")
    conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json) "
        "VALUES ('s', 'pre', 'Bash', 1000, '{\"a\": 1}')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CCLOG_DB", db_path)
    cmd_prune(argparse.Namespace(days=30, dry_run=False, no_vacuum=True))
    assert "Stripped JSON from 1 event" in capsys.readouterr().out


def test_retention_days_from_env_parsing(monkeypatch):
    from cclog.prune import retention_days_from_env

    monkeypatch.delenv("CCLOG_RETENTION_DAYS", raising=False)
    assert retention_days_from_env() is None
    for bad in ("abc", "0", "-3", ""):
        monkeypatch.setenv("CCLOG_RETENTION_DAYS", bad)
        assert retention_days_from_env() is None
    monkeypatch.setenv("CCLOG_RETENTION_DAYS", "14")
    assert retention_days_from_env() == 14


def test_cli_prune_dry_run_does_not_modify(tmp_path, monkeypatch, capsys):
    import argparse
    from cclog.cli import cmd_prune
    from cclog.db import get_db

    db_path = str(tmp_path / "audit.db")
    conn = get_db(db_path)
    conn.execute("INSERT INTO sessions (id, started_at, model, cwd) VALUES ('s', 1, 'm', '/')")
    conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json) "
        "VALUES ('s', 'pre', 'Bash', 1000, '{\"a\": 1}')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CCLOG_DB", db_path)
    cmd_prune(argparse.Namespace(days=30, dry_run=True, no_vacuum=False))
    out = capsys.readouterr().out
    assert "Would strip" in out
    assert "1" in out

    conn = get_db(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM events WHERE input_json IS NOT NULL"
    ).fetchone()[0] == 1
    conn.close()


def test_cli_prune_strips_and_reports(tmp_path, monkeypatch, capsys):
    import argparse
    from cclog.cli import cmd_prune
    from cclog.db import get_db

    db_path = str(tmp_path / "audit.db")
    conn = get_db(db_path)
    conn.execute("INSERT INTO sessions (id, started_at, model, cwd) VALUES ('s', 1, 'm', '/')")
    conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json) "
        "VALUES ('s', 'pre', 'Bash', 1000, '{\"a\": 1}')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CCLOG_DB", db_path)
    cmd_prune(argparse.Namespace(days=30, dry_run=False, no_vacuum=False))
    out = capsys.readouterr().out
    assert "Stripped JSON from 1 event" in out

    conn = get_db(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM events WHERE input_json IS NOT NULL"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    conn.close()
