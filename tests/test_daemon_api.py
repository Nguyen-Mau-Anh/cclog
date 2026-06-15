"""Tests for the v2 dashboard API: paginated events, lean session detail,
tool breakdown, and static dist serving."""
from __future__ import annotations

import json
import socket
import threading
import time
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cclog.daemon import CclogDaemon
from cclog.db import get_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _short_sock_path() -> str:
    """AF_UNIX paths are limited to ~104 chars on macOS; pytest tmp_path
    routinely exceeds that. Use a short /tmp dir instead."""
    return str(Path(tempfile.mkdtemp(prefix="cclog-", dir="/tmp")) / "cclog.sock")


class _DaemonFixture:
    def __init__(self, db_path: str, sock_path: str, port: int):
        self.daemon = CclogDaemon(
            db_path=db_path, sock_path=sock_path, host="127.0.0.1", port=port
        )
        self.port = port
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "CclogDaemon":
        self._thread = threading.Thread(target=self.daemon.start, daemon=True)
        self._thread.start()
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


def _seed_session(db_path: str, session_id: str, n_events: int, base_ts: int = 1_000_000) -> None:
    """Insert a session with n_events post events at base_ts, base_ts+1000, ..."""
    conn = get_db(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, started_at, model, cwd) VALUES (?,?,?,?)",
            (session_id, base_ts, "claude-sonnet-4-6", "/tmp/proj"),
        )
        for i in range(n_events):
            tool = "Bash" if i % 2 == 0 else "Read"
            conn.execute(
                "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json, output_json) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, "post", tool, base_ts + i * 1000, '{"i":%d}' % i, None),
            )
            event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO token_ledger "
                "(event_id, gross_input, gross_output, net_input, cost_usd, counted_by, model) "
                "VALUES (?,?,?,?,?,?,?)",
                (event_id, 100, 10, 100, 0.001, "api", "claude-sonnet-4-6"),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_pre_event(
    db_path: str, session_id: str, ts: int, tool: str = "Bash"
) -> int:
    """Insert a single pre-phase event with NO token_ledger row."""
    conn = get_db(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, started_at, model, cwd) VALUES (?,?,?,?)",
            (session_id, ts, "claude-sonnet-4-6", "/tmp/proj"),
        )
        conn.execute(
            "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json, output_json) "
            "VALUES (?,?,?,?,?,?)",
            (session_id, "pre", tool, ts, '{"pre":true}', None),
        )
        event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return event_id
    finally:
        conn.close()


def _send_hook_event(sock_path: str, payload: dict) -> None:
    """Send a JSON hook event over a Unix socket (newline-terminated)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(sock_path)
        s.sendall(json.dumps(payload).encode() + b"\n")


def _get_json(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as resp:
        return resp.status, json.loads(resp.read())


# ---------------------------------------------------------------------------
# /api/sessions/<id>/events — pagination
# ---------------------------------------------------------------------------

def test_events_endpoint_default_limit_and_order(tmp_path):
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-page", 250)
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        status, body = _get_json(port, "/api/sessions/sess-page/events")
    assert status == 200
    assert body["has_more"] is True
    events = body["events"]
    assert len(events) == 200  # default limit
    # Newest first
    times = [e["occurred_at"] for e in events]
    assert times == sorted(times, reverse=True)
    # Ledger fields joined in
    assert events[0]["gross_input"] == 100
    assert events[0]["counted_by"] == "api"


def test_events_endpoint_limit_param(tmp_path):
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-lim", 30)
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        status, body = _get_json(port, "/api/sessions/sess-lim/events?limit=10")
        assert len(body["events"]) == 10
        assert body["has_more"] is True
        # limit larger than data → everything, no more
        status, body = _get_json(port, "/api/sessions/sess-lim/events?limit=100")
        assert len(body["events"]) == 30
        assert body["has_more"] is False


def test_events_endpoint_before_cursor_pages_backwards(tmp_path):
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-cursor", 50)  # ts 1_000_000 .. 1_049_000
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        _, page1 = _get_json(port, "/api/sessions/sess-cursor/events?limit=20")
        oldest = page1["events"][-1]["occurred_at"]
        _, page2 = _get_json(
            port, f"/api/sessions/sess-cursor/events?limit=20&before={oldest}"
        )
    assert len(page2["events"]) == 20
    # Strictly older than the cursor — no overlap
    assert max(e["occurred_at"] for e in page2["events"]) < oldest
    ids1 = {e["id"] for e in page1["events"]}
    ids2 = {e["id"] for e in page2["events"]}
    assert not ids1 & ids2


def test_events_endpoint_cursor_handles_same_millisecond_boundary(tmp_path):
    """Events sharing one occurred_at millisecond across a page boundary must
    all be reachable via the (before, before_id) keyset cursor — a bare
    timestamp cursor would skip the rest of the cluster."""
    db_path = str(tmp_path / "audit.db")
    conn = get_db(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (id, started_at, model, cwd) VALUES (?,?,?,?)",
            ("sess-samems", 1_000_000, "m", "/tmp"),
        )
        # 10 events, ALL at the same millisecond
        for i in range(10):
            conn.execute(
                "INSERT INTO events (session_id, phase, tool_name, occurred_at) VALUES (?,?,?,?)",
                ("sess-samems", "pre", f"Tool{i}", 1_000_000),
            )
        conn.commit()
    finally:
        conn.close()

    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        seen = set()
        before, before_id = 0, 0
        for _ in range(5):  # paginate in pages of 3
            qs = f"limit=3&before={before}&before_id={before_id}" if before else "limit=3"
            _, body = _get_json(port, f"/api/sessions/sess-samems/events?{qs}")
            for e in body["events"]:
                assert e["id"] not in seen, "cursor returned a duplicate"
                seen.add(e["id"])
            if not body["has_more"]:
                break
            last = body["events"][-1]
            before, before_id = last["occurred_at"], last["id"]
    assert len(seen) == 10, f"keyset cursor lost events: got {len(seen)}/10"


def test_events_endpoint_since_filters_window(tmp_path):
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-since", 50)  # ts 1_000_000 .. 1_049_000
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        _, body = _get_json(
            port, "/api/sessions/sess-since/events?since=1040000"
        )
    assert len(body["events"]) == 10  # ts 1_040_000 .. 1_049_000 inclusive
    assert all(e["occurred_at"] >= 1_040_000 for e in body["events"])
    assert body["has_more"] is False


def test_events_endpoint_clamps_bad_params(tmp_path):
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-bad", 5)
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        # Garbage params must not 500
        status, body = _get_json(
            port, "/api/sessions/sess-bad/events?limit=zzz&since=-5&before=nope"
        )
        assert status == 200
        assert len(body["events"]) == 5
        # limit hard-capped at 1000
        status, body = _get_json(port, "/api/sessions/sess-bad/events?limit=999999")
        assert status == 200


def test_events_endpoint_unknown_session_404(tmp_path):
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-x", 1)
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get_json(port, "/api/sessions/nope/events")
        assert exc.value.code == 404


# ---------------------------------------------------------------------------
# /api/sessions/<id> — lean detail with tool breakdown
# ---------------------------------------------------------------------------

def test_session_detail_has_breakdown_and_no_events(tmp_path):
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-detail", 10)  # 5 Bash + 5 Read
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        status, body = _get_json(port, "/api/sessions/sess-detail")
    assert status == 200
    assert "events" not in body, "detail endpoint must not embed events"
    assert body["tool_calls"] == 10
    breakdown = {b["tool_name"]: b for b in body["tool_breakdown"]}
    assert breakdown["Bash"]["calls"] == 5
    assert breakdown["Read"]["calls"] == 5
    assert breakdown["Bash"]["tokens"] == 5 * 110
    assert breakdown["Bash"]["cost_usd"] == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# Static dist serving
# ---------------------------------------------------------------------------

def test_serves_dist_index_and_assets(tmp_path, monkeypatch):
    import cclog.daemon as daemon_mod

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>SPA</body></html>")
    (dist / "assets" / "index-abc123.js").write_text("console.log('hi')")
    monkeypatch.setattr(daemon_mod, "_DIST_DIR", dist)

    db_path = str(tmp_path / "audit.db")
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
            assert resp.status == 200
            assert b"SPA" in resp.read()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/assets/index-abc123.js", timeout=3
        ) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("application/javascript")

        # Path traversal must be rejected
        secret = tmp_path / "secret.txt"
        secret.write_text("nope")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/assets/../../secret.txt", timeout=3
            )
        assert exc.value.code in (400, 404)


# ---------------------------------------------------------------------------
# Pre-phase events — NULL ledger fields via LEFT JOIN
# ---------------------------------------------------------------------------

def test_events_endpoint_includes_pre_phase_with_null_ledger(tmp_path):
    """Pre-phase events have no token_ledger row; the page must still include
    them, with all ledger fields null."""
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-pre", 4)  # post events at ts 1_000_000..1_003_000
    _insert_pre_event(db_path, "sess-pre", 1_004_000, tool="Write")
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        status, body = _get_json(port, "/api/sessions/sess-pre/events")
    assert status == 200
    assert len(body["events"]) == 5
    # Newest first → the pre event leads the page
    pre = body["events"][0]
    assert pre["phase"] == "pre"
    assert pre["tool_name"] == "Write"
    assert pre["gross_input"] is None
    assert pre["gross_output"] is None
    assert pre["net_input"] is None
    assert pre["cost_usd"] is None
    assert pre["counted_by"] is None
    # Post events keep their ledger values
    assert body["events"][1]["phase"] == "post"
    assert body["events"][1]["gross_input"] == 100


def test_session_detail_breakdown_counts_pre_events_as_zero_token_calls(tmp_path):
    """Pre-phase events count as calls in tool_breakdown but contribute
    zero tokens/cost (COALESCE over NULL ledger)."""
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-pre-bd", 2)  # 1 Bash + 1 Read post, 110 tokens each
    _insert_pre_event(db_path, "sess-pre-bd", 1_002_000, tool="Bash")
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        status, body = _get_json(port, "/api/sessions/sess-pre-bd")
    assert status == 200
    assert body["tool_calls"] == 3
    breakdown = {b["tool_name"]: b for b in body["tool_breakdown"]}
    assert breakdown["Bash"]["calls"] == 2  # 1 post + 1 pre
    assert breakdown["Bash"]["tokens"] == 110  # pre adds nothing
    assert breakdown["Bash"]["cost_usd"] == pytest.approx(0.001)
    assert breakdown["Read"]["calls"] == 1


# ---------------------------------------------------------------------------
# Pagination boundaries
# ---------------------------------------------------------------------------

def test_events_endpoint_has_more_boundary_at_default_limit(tmp_path):
    """Exactly 200 events → full page, has_more False; 201 → has_more True."""
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-200", 200)
    _seed_session(db_path, "sess-201", 201)
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        _, body200 = _get_json(port, "/api/sessions/sess-200/events")
        _, body201 = _get_json(port, "/api/sessions/sess-201/events")
    assert len(body200["events"]) == 200
    assert body200["has_more"] is False
    assert len(body201["events"]) == 200
    assert body201["has_more"] is True


def test_events_endpoint_since_and_before_combined(tmp_path):
    """since (inclusive) + before (exclusive) bound a window together."""
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-window", 50)  # ts 1_000_000 .. 1_049_000
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        _, body = _get_json(
            port,
            "/api/sessions/sess-window/events?since=1010000&before=1020000",
        )
    events = body["events"]
    assert len(events) == 10  # ts 1_010_000 .. 1_019_000
    assert all(1_010_000 <= e["occurred_at"] < 1_020_000 for e in events)
    assert body["has_more"] is False


# ---------------------------------------------------------------------------
# /api/sessions/<id> — 404
# ---------------------------------------------------------------------------

def test_session_detail_unknown_session_404(tmp_path):
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-real", 1)
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get_json(port, "/api/sessions/does-not-exist")
        assert exc.value.code == 404


# ---------------------------------------------------------------------------
# /api/events/<id> — lazy JSON loading
# ---------------------------------------------------------------------------

def test_event_json_endpoint_returns_parsed_json_and_404(tmp_path):
    """The lazy-load endpoint returns parsed input/output JSON for a real
    event id and 404 for an unknown one. Deliberately does NOT set CCLOG_DB:
    the endpoint must resolve the DB via daemon.db_path like every other
    handler."""
    db_path = str(tmp_path / "audit.db")
    _seed_session(db_path, "sess-json", 1)  # event input_json = {"i":0}
    event_id = _insert_pre_event(db_path, "sess-json", 1_001_000)  # {"pre":true}
    port = _free_port()
    with _DaemonFixture(db_path, _short_sock_path(), port):
        status, body = _get_json(port, f"/api/events/{event_id}")
        assert status == 200
        assert body["input_json"] == {"pre": True}
        assert body["output_json"] is None

        with pytest.raises(urllib.error.HTTPError) as exc:
            _get_json(port, "/api/events/999999")
        assert exc.value.code == 404


# ---------------------------------------------------------------------------
# SSE — pre-phase payload
# ---------------------------------------------------------------------------

def test_sse_pre_phase_event_payload_has_null_token_fields(tmp_path):
    """A pre-phase hook event broadcast over SSE carries an embedded summary
    with a real event id but null token/cost fields (no ledger written)."""
    sock_path = _short_sock_path()
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
                        break
        except Exception:
            pass
        finally:
            sse_done.set()

    payload = {
        "session_id": "sess-sse-pre",
        "_phase": "pre",
        "tool_name": "Grep",
        "tool_input": {"pattern": "foo"},
        "model": "claude-sonnet-4-6",
        "cwd": "/tmp",
    }

    with _DaemonFixture(db_path, sock_path, port):
        t = threading.Thread(target=sse_reader, daemon=True)
        t.start()
        sse_ready.wait(timeout=3)
        time.sleep(0.1)  # let the client register
        _send_hook_event(sock_path, payload)
        sse_done.wait(timeout=5)

    assert len(received_lines) >= 1, "No SSE data lines received"
    msg = json.loads(received_lines[0][len("data:"):].strip())
    assert msg["type"] == "event"
    assert msg["session_id"] == "sess-sse-pre"
    ev = msg["event"]
    assert ev["phase"] == "pre"
    assert ev["tool_name"] == "Grep"
    assert isinstance(ev["id"], int)
    assert ev["gross_input"] is None
    assert ev["gross_output"] is None
    assert ev["net_input"] is None
    assert ev["cost_usd"] is None
    assert ev["counted_by"] is None
