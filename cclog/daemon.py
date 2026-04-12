"""
cclog.daemon — Persistent daemon that receives hook events over a Unix socket,
writes to SQLite, and serves a web dashboard with Server-Sent Events.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

def _default_db_path() -> str:
    return os.environ.get("CCLOG_DB") or str(Path.home() / ".cclog" / "audit.db")


def _default_sock_path() -> str:
    return os.environ.get("CCLOG_SOCK") or str(Path.home() / ".cclog" / "cclog.sock")


def _default_pid_path() -> str:
    return str(Path.home() / ".cclog" / "daemon.pid")


def _session_timeout_minutes() -> int:
    return int(os.environ.get("CCLOG_SESSION_TIMEOUT_MINUTES") or 30)


def _idle_minutes() -> int:
    return int(os.environ.get("CCLOG_IDLE_MINUTES") or 5)


_MINIMAL_HTML = """<!DOCTYPE html>
<html><head><title>cclog</title></head>
<body>
<h1>cclog Token Audit</h1>
<p>Dashboard not yet available. See <code>/api/sessions</code> for session data.</p>
<script>
const es = new EventSource('/events');
es.onmessage = e => console.log('SSE:', e.data);
</script>
</body></html>
"""

_PACKAGE_DIR = Path(__file__).parent
_INDEX_HTML_PATH = _PACKAGE_DIR / "web" / "index.html"
_APP_JS_PATH = _PACKAGE_DIR / "web" / "app.js"


# ---------------------------------------------------------------------------
# HookEvent dataclass
# ---------------------------------------------------------------------------

@dataclass
class HookEvent:
    session_id: str
    phase: str          # "pre" or "post"
    tool_name: str
    tool_input: dict
    tool_response: dict
    usage: dict         # may contain input_tokens, output_tokens
    model: str
    cwd: str
    received_at: int    # ms since epoch, added by daemon on receipt


# ---------------------------------------------------------------------------
# CclogDaemon
# ---------------------------------------------------------------------------

class CclogDaemon:
    def __init__(
        self,
        db_path: str,
        sock_path: str,
        host: str = "0.0.0.0",
        port: int = 7331,
    ) -> None:
        self.db_path = db_path
        self.sock_path = sock_path
        self.host = host
        self.port = port

        self._db_lock = threading.Lock()
        self._sse_clients: List[Any] = []
        self._sse_lock = threading.Lock()

        self._running = False
        self._http_server: Optional[HTTPServer] = None
        self._socket_thread: Optional[threading.Thread] = None
        self._server_socket: Optional[socket.socket] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start socket listener + HTTP server. Blocks until stop() called."""
        self._running = True

        # Write PID file
        pid_path = Path(_default_pid_path())
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()))

        # Start Unix socket listener in background thread
        self._socket_thread = threading.Thread(
            target=self._run_socket_listener, daemon=True
        )
        self._socket_thread.start()

        # Start HTTP server (blocks until stop())
        self._start_http_server()

    def stop(self) -> None:
        """Signal daemon to shut down."""
        self._running = False

        # Close server socket to unblock accept()
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass

        # Shut down HTTP server
        if self._http_server:
            self._http_server.shutdown()

        # Clean up socket file
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass

        # Remove PID file
        try:
            os.unlink(_default_pid_path())
        except OSError:
            pass

        # Close all SSE clients
        with self._sse_lock:
            for wfile in list(self._sse_clients):
                try:
                    wfile.close()
                except Exception:
                    pass
            self._sse_clients.clear()

    # ------------------------------------------------------------------
    # Unix socket listener
    # ------------------------------------------------------------------

    def _run_socket_listener(self) -> None:
        # Remove stale socket file
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass

        # Ensure parent directory exists
        Path(self.sock_path).parent.mkdir(parents=True, exist_ok=True)

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket = srv
        srv.bind(self.sock_path)
        srv.listen(64)
        srv.settimeout(1.0)  # so we can check _running

        while self._running:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=self._handle_connection, args=(conn,), daemon=True
            )
            t.start()

    def _handle_connection(self, conn: socket.socket) -> None:
        try:
            data = b""
            conn.settimeout(5.0)
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            if not data:
                return
            raw = data.rstrip(b"\n")
            try:
                payload: Dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                return

            # Route JSONL token updates to their own handler
            if payload.get("type") == "jsonl_token_update":
                self._update_jsonl_tokens(payload)
                return

            phase = payload.get("_phase") or payload.get("phase") or "post"
            received_at = int(time.time() * 1000)

            session_name = payload.get("session_name") or os.environ.get("CLAUDE_SESSION_NAME")

            event = HookEvent(
                session_id=payload.get("session_id") or str(uuid.uuid4()),
                phase=phase,
                tool_name=payload.get("tool_name") or "unknown",
                tool_input=payload.get("tool_input") or {},
                tool_response=payload.get("tool_response") or {},
                usage=payload.get("usage") or {},
                model=payload.get("model") or "unknown",
                cwd=payload.get("cwd") or "",
                received_at=received_at,
            )
            self._handle_event(event, session_name=session_name)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _handle_event(self, event: HookEvent, session_name: Optional[str] = None) -> None:
        self._write_to_db(event, session_name=session_name)
        self._broadcast_sse(event)

    def _get_prev_gross_input(self, conn: Any, session_id: str) -> int:
        row = conn.execute(
            "SELECT tl.gross_input FROM token_ledger tl "
            "JOIN events e ON e.id = tl.event_id "
            "WHERE e.session_id = ? ORDER BY e.occurred_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return row[0] if row else 0

    def _write_to_db(self, event: HookEvent, session_name: Optional[str] = None) -> None:
        from cclog.db import get_db
        from cclog.tokens import count_tokens
        from cclog.pricing import get_cost_usd

        with self._db_lock:
            conn = get_db(self.db_path)
            try:
                # Upsert session
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (id, started_at, model, cwd) VALUES (?,?,?,?)",
                    (event.session_id, event.received_at, event.model, event.cwd),
                )
                if session_name:
                    conn.execute(
                        "UPDATE sessions SET name = ? WHERE id = ? AND name IS NULL",
                        (session_name, event.session_id),
                    )

                # Insert event
                input_json = json.dumps(event.tool_input) if event.tool_input else None
                output_json = json.dumps(event.tool_response) if event.tool_response else None
                conn.execute(
                    "INSERT INTO events "
                    "(session_id, phase, tool_name, occurred_at, input_json, output_json) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        event.session_id,
                        event.phase,
                        event.tool_name,
                        event.received_at,
                        input_json,
                        output_json,
                    ),
                )
                event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                # Token ledger (post only)
                if event.phase == "post":
                    prev = self._get_prev_gross_input(conn, event.session_id)
                    # Build payload-like dict for count_tokens
                    payload_like = {
                        "usage": event.usage,
                        "tool_input": event.tool_input,
                        "tool_response": event.tool_response,
                    }
                    result = count_tokens(payload_like, prev_gross_input=prev)
                    model = event.model or "unknown"
                    cost = get_cost_usd(model, result.gross_input, result.gross_output)
                    conn.execute(
                        "INSERT INTO token_ledger "
                        "(event_id, gross_input, gross_output, net_input, cost_usd, counted_by, model) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            event_id,
                            result.gross_input,
                            result.gross_output,
                            result.net_input,
                            cost,
                            result.counted_by,
                            model,
                        ),
                    )

                conn.commit()
            finally:
                conn.close()

    def _update_jsonl_tokens(self, data: dict) -> None:
        """Update session with JSONL-sourced accurate token totals."""
        from cclog.db import get_db

        session_id = data.get("session_id")
        if not session_id:
            return

        with self._db_lock:
            conn = get_db(self.db_path)
            try:
                # Ensure session exists (Stop hook may arrive before tool hooks)
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (id, started_at, model, cwd) VALUES (?,?,?,?)",
                    (session_id, int(time.time() * 1000), data.get("jsonl_model"), ""),
                )
                conn.execute(
                    """
                    UPDATE sessions SET
                        jsonl_input_tokens = ?,
                        jsonl_output_tokens = ?,
                        jsonl_cache_creation_tokens = ?,
                        jsonl_cache_read_tokens = ?,
                        jsonl_cost_usd = ?,
                        jsonl_model = ?
                    WHERE id = ?
                    """,
                    (
                        data.get("jsonl_input_tokens", 0),
                        data.get("jsonl_output_tokens", 0),
                        data.get("jsonl_cache_creation_tokens", 0),
                        data.get("jsonl_cache_read_tokens", 0),
                        data.get("jsonl_cost_usd", 0.0),
                        data.get("jsonl_model"),
                        session_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        # Broadcast SSE so dashboard refreshes
        self._broadcast_sse_dict({"type": "session_update", "session_id": session_id})

    # ------------------------------------------------------------------
    # SSE broadcasting
    # ------------------------------------------------------------------

    def _broadcast_sse(self, event: HookEvent) -> None:
        self._broadcast_sse_dict({
            "type": "session_update",
            "session_id": event.session_id,
            "timestamp": event.received_at,
        })

    def _broadcast_sse_dict(self, msg: dict) -> None:
        """Broadcast an arbitrary dict as an SSE event."""
        data = f"data: {json.dumps(msg)}\n\n".encode()
        with self._sse_lock:
            dead: List[Any] = []
            for wfile in self._sse_clients:
                try:
                    wfile.write(data)
                    wfile.flush()
                except Exception:
                    dead.append(wfile)
            for wfile in dead:
                self._sse_clients.remove(wfile)

    def _register_sse_client(self, wfile: Any) -> None:
        with self._sse_lock:
            self._sse_clients.append(wfile)

    def _unregister_sse_client(self, wfile: Any) -> None:
        with self._sse_lock:
            try:
                self._sse_clients.remove(wfile)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # HTTP server
    # ------------------------------------------------------------------

    def _start_http_server(self) -> None:
        daemon = self  # capture for handler closure

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                pass  # suppress access logs

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]

                if path == "/":
                    self._serve_index()
                elif path == "/app.js":
                    self._serve_app_js()
                elif path == "/api/sessions":
                    self._serve_sessions()
                elif path.startswith("/api/sessions/"):
                    session_id = path[len("/api/sessions/"):]
                    self._serve_session_detail(session_id)
                elif path == "/api/status":
                    self._serve_status()
                elif path == "/events":
                    self._serve_sse()
                else:
                    self.send_error(404, "Not Found")

            def _serve_index(self) -> None:
                if _INDEX_HTML_PATH.exists():
                    content = _INDEX_HTML_PATH.read_bytes()
                    content_type = "text/html"
                else:
                    content = _MINIMAL_HTML.encode()
                    content_type = "text/html"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def _serve_app_js(self) -> None:
                if not _APP_JS_PATH.exists():
                    self.send_error(404, "Not Found")
                    return
                content = _APP_JS_PATH.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def _serve_sessions(self) -> None:
                from cclog.db import get_db

                now_ms = int(time.time() * 1000)
                idle_threshold = _idle_minutes() * 60 * 1000
                closed_threshold = _session_timeout_minutes() * 60 * 1000

                with daemon._db_lock:
                    conn = get_db(daemon.db_path)
                    try:
                        rows = conn.execute(
                            """
                            SELECT
                                s.id,
                                s.name,
                                s.cwd,
                                s.started_at,
                                MAX(e.occurred_at) AS last_active,
                                COUNT(e.id) AS tool_calls,
                                COALESCE(SUM(tl.gross_input + tl.gross_output), 0) AS total_tokens,
                                COALESCE(SUM(tl.cost_usd), 0.0) AS estimated_cost,
                                s.jsonl_input_tokens,
                                s.jsonl_output_tokens,
                                s.jsonl_cache_creation_tokens,
                                s.jsonl_cache_read_tokens,
                                s.jsonl_cost_usd,
                                s.jsonl_model
                            FROM sessions s
                            LEFT JOIN events e ON e.session_id = s.id
                            LEFT JOIN token_ledger tl ON tl.event_id = e.id
                            GROUP BY s.id
                            ORDER BY last_active DESC NULLS LAST
                            """
                        ).fetchall()
                    finally:
                        conn.close()

                result = []
                for row in rows:
                    last_active = row["last_active"]
                    if last_active is None:
                        status = "closed"
                    else:
                        age_ms = now_ms - last_active
                        if age_ms <= idle_threshold:
                            status = "active"
                        elif age_ms <= closed_threshold:
                            status = "idle"
                        else:
                            status = "closed"

                    result.append({
                        "id": row["id"],
                        "name": row["name"],
                        "cwd": row["cwd"],
                        "started_at": row["started_at"],
                        "last_active": last_active,
                        "status": status,
                        "tool_calls": row["tool_calls"],
                        "total_tokens": row["total_tokens"],
                        "estimated_cost": row["estimated_cost"],
                        "jsonl_input_tokens": row["jsonl_input_tokens"],
                        "jsonl_output_tokens": row["jsonl_output_tokens"],
                        "jsonl_cache_creation_tokens": row["jsonl_cache_creation_tokens"],
                        "jsonl_cache_read_tokens": row["jsonl_cache_read_tokens"],
                        "jsonl_cost_usd": row["jsonl_cost_usd"],
                        "jsonl_model": row["jsonl_model"],
                    })

                self._send_json(result)

            def _serve_session_detail(self, session_id: str) -> None:
                from cclog.db import get_db

                now_ms = int(time.time() * 1000)
                idle_threshold = _idle_minutes() * 60 * 1000
                closed_threshold = _session_timeout_minutes() * 60 * 1000

                found = False
                row = None
                event_rows = []

                with daemon._db_lock:
                    conn = get_db(daemon.db_path)
                    try:
                        sess_rows = conn.execute(
                            """
                            SELECT
                                s.id,
                                s.name,
                                s.cwd,
                                s.started_at,
                                MAX(e.occurred_at) AS last_active,
                                COUNT(e.id) AS tool_calls,
                                COALESCE(SUM(tl.gross_input + tl.gross_output), 0) AS total_tokens,
                                COALESCE(SUM(tl.cost_usd), 0.0) AS estimated_cost,
                                s.jsonl_input_tokens,
                                s.jsonl_output_tokens,
                                s.jsonl_cache_creation_tokens,
                                s.jsonl_cache_read_tokens,
                                s.jsonl_cost_usd,
                                s.jsonl_model
                            FROM sessions s
                            LEFT JOIN events e ON e.session_id = s.id
                            LEFT JOIN token_ledger tl ON tl.event_id = e.id
                            WHERE s.id = ?
                            GROUP BY s.id
                            """,
                            (session_id,),
                        ).fetchall()

                        if sess_rows:
                            found = True
                            row = sess_rows[0]
                            event_rows = conn.execute(
                                """
                                SELECT
                                    e.id,
                                    e.phase,
                                    e.tool_name,
                                    e.occurred_at,
                                    tl.gross_input,
                                    tl.gross_output,
                                    tl.net_input,
                                    tl.cost_usd,
                                    tl.counted_by
                                FROM events e
                                LEFT JOIN token_ledger tl ON tl.event_id = e.id
                                WHERE e.session_id = ?
                                ORDER BY e.occurred_at ASC
                                """,
                                (session_id,),
                            ).fetchall()
                    finally:
                        conn.close()

                if not found:
                    self.send_error(404, "Session not found")
                    return

                last_active = row["last_active"]
                if last_active is None:
                    status = "closed"
                else:
                    age_ms = now_ms - last_active
                    if age_ms <= idle_threshold:
                        status = "active"
                    elif age_ms <= closed_threshold:
                        status = "idle"
                    else:
                        status = "closed"

                events_list = []
                for er in event_rows:
                    events_list.append({
                        "id": er["id"],
                        "phase": er["phase"],
                        "tool_name": er["tool_name"],
                        "occurred_at": er["occurred_at"],
                        "gross_input": er["gross_input"],
                        "gross_output": er["gross_output"],
                        "net_input": er["net_input"],
                        "cost_usd": er["cost_usd"],
                        "counted_by": er["counted_by"],
                    })

                result = {
                    "id": row["id"],
                    "name": row["name"],
                    "cwd": row["cwd"],
                    "started_at": row["started_at"],
                    "last_active": last_active,
                    "status": status,
                    "tool_calls": row["tool_calls"],
                    "total_tokens": row["total_tokens"],
                    "estimated_cost": row["estimated_cost"],
                    "jsonl_input_tokens": row["jsonl_input_tokens"],
                    "jsonl_output_tokens": row["jsonl_output_tokens"],
                    "jsonl_cache_creation_tokens": row["jsonl_cache_creation_tokens"],
                    "jsonl_cache_read_tokens": row["jsonl_cache_read_tokens"],
                    "jsonl_cost_usd": row["jsonl_cost_usd"],
                    "jsonl_model": row["jsonl_model"],
                    "events": events_list,
                }
                self._send_json(result)

            def _serve_status(self) -> None:
                from cclog.db import get_db

                now_ms = int(time.time() * 1000)
                idle_threshold = _idle_minutes() * 60 * 1000

                with daemon._db_lock:
                    conn = get_db(daemon.db_path)
                    try:
                        rows = conn.execute(
                            """
                            SELECT s.id, MAX(e.occurred_at) AS last_active
                            FROM sessions s
                            LEFT JOIN events e ON e.session_id = s.id
                            GROUP BY s.id
                            """
                        ).fetchall()
                    finally:
                        conn.close()

                active_count = 0
                for row in rows:
                    la = row["last_active"]
                    if la is not None and (now_ms - la) <= idle_threshold:
                        active_count += 1

                self._send_json({
                    "status": "running",
                    "port": daemon.port,
                    "active_sessions": active_count,
                })

            def _serve_sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                wfile = self.wfile
                daemon._register_sse_client(wfile)
                try:
                    # Keep connection open until client disconnects
                    while daemon._running:
                        try:
                            # Send a heartbeat comment every second to detect disconnect
                            wfile.write(b": heartbeat\n\n")
                            wfile.flush()
                            time.sleep(1.0)
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            break
                finally:
                    daemon._unregister_sse_client(wfile)

            def _send_json(self, data: Any) -> None:
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
            daemon_threads = True

            def handle_error(self, request: Any, client_address: Any) -> None:
                # Suppress noisy connection-reset errors on shutdown
                import sys as _sys
                exc = _sys.exc_info()[1]
                if isinstance(exc, (ValueError, BrokenPipeError, ConnectionResetError)):
                    return
                super().handle_error(request, client_address)

        self._http_server = ThreadedHTTPServer((self.host, self.port), Handler)
        self._http_server.serve_forever()


# ---------------------------------------------------------------------------
# Module-level entry point
# ---------------------------------------------------------------------------

def run_daemon(
    db_path: str | None = None,
    sock_path: str | None = None,
    port: int | None = None,
    host: str | None = None,
) -> None:
    """Start the daemon. Called by `cclog start`."""
    host = host or os.environ.get("CCLOG_HOST", "0.0.0.0")
    port = port or int(os.environ.get("CCLOG_PORT", "7331"))
    resolved_db = db_path or _default_db_path()
    resolved_sock = sock_path or _default_sock_path()

    d = CclogDaemon(
        db_path=resolved_db,
        sock_path=resolved_sock,
        host=host,
        port=port,
    )
    d.start()


if __name__ == "__main__":
    run_daemon()
