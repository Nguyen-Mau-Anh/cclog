"""Tests for cclog.hook — hook entry point."""
import io
import json
import os
import socket
import sqlite3
import threading
import pytest

from cclog.db import get_db
from cclog.hook import main


def _run_hook(phase: str, payload: dict, db_path: str, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setenv("CCLOG_DB", db_path)
    # Ensure no daemon socket exists — force fallback to direct SQLite write
    monkeypatch.setenv("CCLOG_SOCK", "/tmp/__cclog_nonexistent_sock_for_tests__")
    main(phase)


def test_post_hook_creates_session(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit.db")
    payload = {
        "session_id": "sess-1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": {"output": "file.txt\n"},
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }
    _run_hook("post", payload, db_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM sessions WHERE id='sess-1'").fetchone()
    assert row is not None
    conn.close()


def test_post_hook_creates_event(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit.db")
    payload = {
        "session_id": "sess-1",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.py"},
        "tool_response": {"output": "# code"},
        "usage": {"input_tokens": 200, "output_tokens": 5},
    }
    _run_hook("post", payload, db_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT tool_name, phase FROM events WHERE session_id='sess-1'").fetchone()
    assert row[0] == "Read"
    assert row[1] == "post"
    conn.close()


def test_post_hook_writes_token_ledger(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit.db")
    payload = {
        "session_id": "sess-1",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd"},
        "tool_response": {"output": "/home/user\n"},
        "usage": {"input_tokens": 300, "output_tokens": 8},
    }
    _run_hook("post", payload, db_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT gross_input, counted_by FROM token_ledger").fetchone()
    assert row[0] == 300
    assert row[1] == "api"
    conn.close()


def test_pre_hook_creates_event(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit.db")
    payload = {"session_id": "sess-1", "tool_name": "Edit", "tool_input": {"file_path": "x.py"}}
    _run_hook("pre", payload, db_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT phase FROM events WHERE session_id='sess-1'").fetchone()
    assert row[0] == "pre"
    conn.close()


def test_hook_missing_session_id_generates_uuid(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit.db")
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    _run_hook("post", payload, db_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM sessions").fetchone()
    assert row is not None
    assert len(row[0]) > 0
    conn.close()


def test_hook_malformed_json_exits_zero(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "audit.db")
    monkeypatch.setattr("sys.stdin", io.StringIO("NOT JSON {{{"))
    monkeypatch.setenv("CCLOG_DB", db_path)
    monkeypatch.setenv("CCLOG_SOCK", "/tmp/__cclog_nonexistent_sock_for_tests__")
    # Must not raise — exits 0
    main("post")


def test_hook_always_exits_zero_on_error(tmp_path, monkeypatch):
    """Even with a completely broken payload, main() must not raise."""
    db_path = str(tmp_path / "audit.db")
    monkeypatch.setattr("sys.stdin", io.StringIO("null"))
    monkeypatch.setenv("CCLOG_DB", db_path)
    monkeypatch.setenv("CCLOG_SOCK", "/tmp/__cclog_nonexistent_sock_for_tests__")
    main("post")  # must not raise


def test_hook_sends_to_daemon_when_socket_exists(tmp_path, monkeypatch):
    """When a Unix socket server is listening, hook sends JSON line and skips DB."""
    sock_path = str(tmp_path / "test_daemon.sock")
    received = []
    ready = threading.Event()
    done = threading.Event()

    def server():
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)
        ready.set()
        conn, _ = srv.accept()
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        received.append(data.rstrip(b"\n"))
        conn.close()
        srv.close()
        done.set()

    t = threading.Thread(target=server, daemon=True)
    t.start()
    assert ready.wait(timeout=2), "Server thread did not become ready"

    db_path = str(tmp_path / "audit.db")
    payload = {
        "session_id": "sess-daemon",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
    }

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setenv("CCLOG_SOCK", sock_path)
    monkeypatch.setenv("CCLOG_DB", db_path)
    main("pre")

    assert done.wait(timeout=2), "Server thread did not complete — hook may not have sent"

    # Verify the message was sent to the socket
    assert len(received) == 1
    sent = json.loads(received[0])
    assert sent["session_id"] == "sess-daemon"
    assert sent["_phase"] == "pre"

    # Verify the DB was NOT written (daemon handled it)
    assert not os.path.exists(db_path)


def test_hook_falls_back_to_direct_write_when_no_daemon(tmp_path, monkeypatch):
    """When socket path does not exist, hook falls back to direct SQLite write."""
    db_path = str(tmp_path / "audit.db")
    nonexistent_sock = str(tmp_path / "no_such.sock")

    payload = {
        "session_id": "sess-fallback",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.py"},
        "tool_response": {"output": "# code"},
        "usage": {"input_tokens": 50, "output_tokens": 5},
    }

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setenv("CCLOG_SOCK", nonexistent_sock)
    monkeypatch.setenv("CCLOG_DB", db_path)
    main("post")

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM sessions WHERE id='sess-fallback'").fetchone()
    assert row is not None
    event_row = conn.execute("SELECT tool_name FROM events WHERE session_id='sess-fallback'").fetchone()
    assert event_row[0] == "Read"
    conn.close()
