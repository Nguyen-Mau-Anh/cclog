"""
cclog.hook — Claude Code hook entry point.

Invoked by Claude Code's PreToolUse / PostToolUse hooks:
    python -m cclog.hook pre
    python -m cclog.hook post

Reads JSON payload from stdin. Tries to send to daemon via Unix socket;
falls back to direct SQLite write if daemon not available. Always exits 0.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict


_ERROR_LOG = Path.home() / ".cclog" / "errors.log"


def _db_path() -> str:
    return os.environ.get("CCLOG_DB") or str(Path.home() / ".cclog" / "audit.db")


def _sock_path() -> str:
    return os.environ.get("CCLOG_SOCK") or str(Path.home() / ".cclog" / "cclog.sock")


def _log_error(msg: str) -> None:
    try:
        _ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_ERROR_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _get_prev_gross_input(conn: Any, session_id: str) -> int:
    row = conn.execute(
        "SELECT tl.gross_input FROM token_ledger tl "
        "JOIN events e ON e.id = tl.event_id "
        "WHERE e.session_id = ? ORDER BY e.occurred_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return row[0] if row else 0


def _write_direct(payload: dict, phase: str) -> None:
    """Write event directly to SQLite (fallback when daemon is not running)."""
    from cclog.db import get_db
    from cclog.tokens import count_tokens
    from cclog.pricing import get_cost_usd

    session_id = payload.get("session_id") or str(uuid.uuid4())
    tool_name = payload.get("tool_name") or "unknown"
    now_ms = int(time.time() * 1000)

    conn = get_db(_db_path())

    # Upsert session
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, started_at, model, cwd) VALUES (?,?,?,?)",
        (session_id, now_ms, payload.get("model"), payload.get("cwd")),
    )

    # Insert event
    input_json = json.dumps(payload.get("tool_input")) if payload.get("tool_input") else None
    output_json = json.dumps(payload.get("tool_response")) if payload.get("tool_response") else None
    conn.execute(
        "INSERT INTO events (session_id, phase, tool_name, occurred_at, input_json, output_json) "
        "VALUES (?,?,?,?,?,?)",
        (session_id, phase, tool_name, now_ms, input_json, output_json),
    )
    event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Token ledger (post only — pre has no response)
    if phase == "post":
        prev = _get_prev_gross_input(conn, session_id)
        result = count_tokens(payload, prev_gross_input=prev)
        model = payload.get("model") or "unknown"
        cost = get_cost_usd(model, result.gross_input, result.gross_output)
        conn.execute(
            "INSERT INTO token_ledger "
            "(event_id, gross_input, gross_output, net_input, cost_usd, counted_by, model) "
            "VALUES (?,?,?,?,?,?,?)",
            (event_id, result.gross_input, result.gross_output,
             result.net_input, cost, result.counted_by, model),
        )

    conn.commit()
    conn.close()


def main(phase: str) -> None:
    try:
        raw = sys.stdin.read()
        try:
            payload: Dict[str, Any] = json.loads(raw)
        except Exception:
            _log_error(f"hook/{phase}: invalid JSON: {raw[:200]}")
            return

        if not isinstance(payload, dict):
            _log_error(f"hook/{phase}: payload is not a dict")
            return

        # Enrich payload with phase
        payload["_phase"] = phase

        # Try Unix socket first
        sock_path = _sock_path()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                sock.connect(sock_path)
                json_bytes = json.dumps(payload).encode()
                sock.sendall(json_bytes + b"\n")
            return
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            pass

        # Fallback: direct SQLite write
        _write_direct(payload, phase)

    except Exception as exc:
        _log_error(f"hook/{phase}: unhandled error: {exc}")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "post"
    main(phase)
