"""Tests for cclog.daemon — Unix socket listener + SQLite writer + HTTP + SSE."""
from __future__ import annotations

import json
import socket
import sqlite3
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from cclog.daemon import CclogDaemon


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _send_hook_event(sock_path: str, payload: dict) -> None:
    """Send a JSON hook event over a Unix socket (newline-terminated)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(sock_path)
        s.sendall(json.dumps(payload).encode() + b"\n")


class _DaemonFixture:
    """Context manager that starts a CclogDaemon in a background thread."""

    def __init__(self, db_path: str, sock_path: str, port: int):
        self.db_path = db_path
        self.sock_path = sock_path
        self.port = port
        self.daemon = CclogDaemon(
            db_path=db_path,
            sock_path=sock_path,
            host="127.0.0.1",
            port=port,
        )
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "CclogDaemon":
        self._thread = threading.Thread(target=self.daemon.start, daemon=True)
        self._thread.start()
        # Wait for socket to appear
        for _ in range(50):
            if Path(self.sock_path).exists():
                break
            time.sleep(0.05)
        # Wait for HTTP server to be ready
        for _ in range(50):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/status", timeout=0.5
                )
                break
            except Exception:
                time.sleep(0.05)
        return self.daemon

    def __exit__(self, *_: object) -> None:
        self.daemon.stop()
        if self._thread:
            self._thread.join(timeout=3)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_daemon_writes_event_on_socket_message(tmp_path):
    """Send a JSON hook event over socket; verify event + session in DB."""
    sock_path = str(tmp_path / "cclog.sock")
    db_path = str(tmp_path / "audit.db")
    port = _free_port()

    payload = {
        "session_id": "sess-socket-write",
        "_phase": "pre",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": {},
        "usage": {},
        "model": "claude-sonnet-4-6",
        "cwd": "/tmp/myproject",
    }

    with _DaemonFixture(db_path, sock_path, port):
        _send_hook_event(sock_path, payload)
        time.sleep(0.2)  # give daemon time to write

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id FROM sessions WHERE id='sess-socket-write'"
    ).fetchone()
    assert row is not None, "Session not written to DB"

    evt = conn.execute(
        "SELECT tool_name, phase FROM events WHERE session_id='sess-socket-write'"
    ).fetchone()
    assert evt is not None, "Event not written to DB"
    assert evt["tool_name"] == "Bash"
    assert evt["phase"] == "pre"
    conn.close()


def test_daemon_writes_token_ledger_for_post_event(tmp_path):
    """Post event with usage field; verify token_ledger row written."""
    sock_path = str(tmp_path / "cclog.sock")
    db_path = str(tmp_path / "audit.db")
    port = _free_port()

    payload = {
        "session_id": "sess-ledger",
        "_phase": "post",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd"},
        "tool_response": {"output": "/home/user\n"},
        "usage": {"input_tokens": 300, "output_tokens": 8},
        "model": "claude-sonnet-4-6",
        "cwd": "/tmp",
    }

    with _DaemonFixture(db_path, sock_path, port):
        _send_hook_event(sock_path, payload)
        time.sleep(0.2)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT tl.gross_input, tl.counted_by FROM token_ledger tl "
        "JOIN events e ON e.id = tl.event_id "
        "WHERE e.session_id = 'sess-ledger'"
    ).fetchone()
    assert row is not None, "Token ledger row not written"
    assert row["gross_input"] == 300
    assert row["counted_by"] == "api"
    conn.close()


def test_daemon_api_sessions_returns_list(tmp_path):
    """Call GET /api/sessions; verify JSON response."""
    sock_path = str(tmp_path / "cclog.sock")
    db_path = str(tmp_path / "audit.db")
    port = _free_port()

    payload = {
        "session_id": "sess-api-list",
        "_phase": "post",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.py"},
        "tool_response": {"output": "# code"},
        "usage": {"input_tokens": 100, "output_tokens": 5},
        "model": "claude-sonnet-4-6",
        "cwd": "/tmp/project",
    }

    with _DaemonFixture(db_path, sock_path, port):
        _send_hook_event(sock_path, payload)
        time.sleep(0.2)

        url = f"http://127.0.0.1:{port}/api/sessions"
        with urllib.request.urlopen(url, timeout=3) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())

    assert isinstance(body, list)
    assert len(body) >= 1
    sess = next((s for s in body if s["id"] == "sess-api-list"), None)
    assert sess is not None, "Expected session not in /api/sessions response"
    assert "tool_calls" in sess
    assert "total_tokens" in sess
    assert "estimated_cost" in sess
    assert "status" in sess


def test_daemon_api_status_returns_running(tmp_path):
    """Call GET /api/status; verify {'status': 'running', ...}."""
    sock_path = str(tmp_path / "cclog.sock")
    db_path = str(tmp_path / "audit.db")
    port = _free_port()

    with _DaemonFixture(db_path, sock_path, port):
        url = f"http://127.0.0.1:{port}/api/status"
        with urllib.request.urlopen(url, timeout=3) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())

    assert body["status"] == "running"
    assert body["port"] == port
    assert "active_sessions" in body


def test_daemon_sse_receives_event_on_hook(tmp_path):
    """Connect to /events, send a hook event, verify SSE data arrives."""
    sock_path = str(tmp_path / "cclog.sock")
    db_path = str(tmp_path / "audit.db")
    port = _free_port()

    received_lines: list[str] = []
    sse_ready = threading.Event()
    sse_done = threading.Event()

    def sse_reader() -> None:
        url = f"http://127.0.0.1:{port}/events"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                sse_ready.set()
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    line = resp.readline()
                    if not line:
                        break
                    decoded = line.decode(errors="replace").rstrip("\n")
                    if decoded.startswith("data:"):
                        received_lines.append(decoded)
                        break  # got our event — exit
        except Exception:
            pass
        finally:
            sse_done.set()

    payload = {
        "session_id": "sess-sse",
        "_phase": "post",
        "tool_name": "Edit",
        "tool_input": {"file_path": "foo.py"},
        "tool_response": {},
        "usage": {"input_tokens": 50, "output_tokens": 3},
        "model": "claude-sonnet-4-6",
        "cwd": "/tmp",
    }

    with _DaemonFixture(db_path, sock_path, port):
        t = threading.Thread(target=sse_reader, daemon=True)
        t.start()

        # Wait for SSE client to be connected
        sse_ready.wait(timeout=3)
        time.sleep(0.1)  # give the client a moment to be registered

        # Send event
        _send_hook_event(sock_path, payload)

        # Wait for SSE reader to receive the event
        sse_done.wait(timeout=5)

    assert len(received_lines) >= 1, "No SSE data lines received"
    data_line = received_lines[0]
    assert data_line.startswith("data:")
    msg = json.loads(data_line[len("data:"):].strip())
    assert msg["type"] == "session_update"
    assert msg["session_id"] == "sess-sse"
