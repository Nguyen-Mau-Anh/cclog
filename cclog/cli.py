"""cclog.cli — command-line entry point."""
from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

_console = Console()


def _db_path() -> str:
    return os.environ.get("CCLOG_DB") or str(Path.home() / ".cclog" / "audit.db")


def _pid_path() -> Path:
    return Path.home() / ".cclog" / "daemon.pid"


def _get_port() -> int:
    return int(os.environ.get("CCLOG_PORT", "7331"))


def _get_host() -> str:
    return os.environ.get("CCLOG_HOST", "0.0.0.0")


def _dashboard_url() -> str:
    return f"http://localhost:{_get_port()}"


def _read_pid() -> Optional[int]:
    try:
        return int(_pid_path().read_text().strip())
    except Exception:
        return None


def _is_daemon_running() -> bool:
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cmd_start(args) -> None:
    host = _get_host()
    port = _get_port()
    url = _dashboard_url()

    if _is_daemon_running():
        _console.print(f"Daemon already running at {url}")
        sys.exit(0)

    subprocess.Popen(
        [sys.executable, "-m", "cclog.daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )

    # Wait up to 3 seconds for PID file to appear
    pid_path = _pid_path()
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if pid_path.exists():
            break
        time.sleep(0.2)

    _console.print(f"Daemon started at {url}")
    webbrowser.open(url)


def cmd_stop(args) -> None:
    pid = _read_pid()
    if pid is None:
        _console.print("Daemon is not running")
        sys.exit(0)

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        _console.print("Daemon is not running")
        _pid_path().unlink(missing_ok=True)
        sys.exit(0)

    # Wait up to 3 seconds for PID file to disappear
    pid_path = _pid_path()
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not pid_path.exists():
            break
        time.sleep(0.1)

    if pid_path.exists():
        _console.print("Daemon did not stop cleanly")
        pid_path.unlink(missing_ok=True)
    else:
        _console.print("Daemon stopped")


def cmd_status(args) -> None:
    import urllib.request
    import urllib.error

    port = _get_port()
    url = _dashboard_url()

    if not _is_daemon_running():
        _console.print("Daemon: stopped")
        return

    pid = _read_pid()
    active_sessions = "?"
    try:
        with urllib.request.urlopen(
            f"http://localhost:{port}/api/status", timeout=1
        ) as resp:
            import json
            data = json.loads(resp.read())
            active_sessions = data.get("active_sessions", "?")
    except Exception:
        pass

    _console.print(f"Daemon: running (PID {pid})")
    _console.print(f"URL:    {url}")
    _console.print(f"Active sessions: {active_sessions}")


def cmd_dashboard(args) -> None:
    url = _dashboard_url()

    if not _is_daemon_running():
        _console.print("Daemon is not running. Start it with: cclog start")
        sys.exit(1)

    _console.print(f"Opening {url}")
    webbrowser.open(url)


def cmd_today(args) -> None:
    db = _db_path()
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        _console.print(f"[red]Error opening database: {e}[/red]")
        sys.exit(1)

    try:
        sql = """
        SELECT
            s.cwd,
            COUNT(DISTINCT e.id) AS tool_calls,
            COALESCE(SUM(tl.gross_input + tl.gross_output), 0) AS total_tokens,
            COALESCE(SUM(tl.cost_usd), 0.0) AS estimated_cost
        FROM sessions s
        LEFT JOIN events e ON e.session_id = s.id AND DATE(e.occurred_at/1000, 'unixepoch') = DATE('now')
        LEFT JOIN token_ledger tl ON tl.event_id = e.id
        GROUP BY s.id
        HAVING tool_calls > 0
        ORDER BY estimated_cost DESC
        """
        rows = conn.execute(sql).fetchall()
    except Exception as e:
        _console.print(f"[red]Query error: {e}[/red]")
        sys.exit(1)
    finally:
        conn.close()

    if not rows:
        _console.print("No sessions logged today.")
        return

    table = Table(title="Today's Token Usage")
    table.add_column("Folder", style="cyan")
    table.add_column("Tool Calls", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Est. Cost", justify="right")

    total_calls = 0
    total_tokens = 0
    total_cost = 0.0

    for row in rows:
        cwd = row["cwd"] or ""
        calls = row["tool_calls"]
        tokens = row["total_tokens"]
        cost = row["estimated_cost"]
        total_calls += calls
        total_tokens += tokens
        total_cost += cost
        table.add_row(cwd, str(calls), str(tokens), f"${cost:.4f}")

    table.add_section()
    table.add_row("TOTAL", str(total_calls), str(total_tokens), f"${total_cost:.4f}", style="bold")

    _console.print(table)


def cmd_sessions(args) -> None:
    db = _db_path()
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        _console.print(f"[red]Error opening database: {e}[/red]")
        sys.exit(1)

    try:
        sql = """
        SELECT s.id, s.name, s.cwd, s.started_at, MAX(e.occurred_at) AS last_active,
               COUNT(e.id) AS tool_calls, COALESCE(SUM(tl.cost_usd), 0.0) AS cost
        FROM sessions s
        LEFT JOIN events e ON e.session_id = s.id
        LEFT JOIN token_ledger tl ON tl.event_id = e.id
        GROUP BY s.id ORDER BY last_active DESC NULLS LAST {limit_clause}
        """.format(limit_clause="" if getattr(args, "all", False) else "LIMIT 20")
        rows = conn.execute(sql).fetchall()
    except Exception as e:
        _console.print(f"[red]Query error: {e}[/red]")
        sys.exit(1)
    finally:
        conn.close()

    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - 24 * 3600 * 1000  # 24 hours ago

    if not getattr(args, "all", False):
        rows = [r for r in rows if r["last_active"] is not None and r["last_active"] >= cutoff_ms]

    if not rows:
        _console.print("No sessions found.")
        return

    table = Table(title="Sessions")
    table.add_column("Status", justify="center")
    table.add_column("Started")
    table.add_column("Last Active")
    table.add_column("Name", style="cyan")
    table.add_column("Folder", style="cyan")
    table.add_column("Calls", justify="right")
    table.add_column("Cost", justify="right")

    active_cutoff_ms = now_ms - 30 * 60 * 1000  # 30 minutes ago

    for row in rows:
        last_active = row["last_active"]
        started_at = row["started_at"]

        if last_active is not None and last_active >= active_cutoff_ms:
            status = "[green]●[/green]"
        else:
            status = "○"

        started_str = _fmt_time(started_at)
        last_str = _fmt_time(last_active)
        cwd = row["cwd"] or ""
        name_display = row["name"] or (cwd).rsplit("/", 1)[-1] or row["id"][:8]
        calls = str(row["tool_calls"])
        cost = f"${row['cost']:.4f}"

        table.add_row(status, started_str, last_str, name_display, cwd, calls, cost)

    _console.print(table)


def _fmt_time(ms: Optional[int]) -> str:
    if ms is None:
        return "—"
    import datetime
    dt = datetime.datetime.fromtimestamp(ms / 1000)
    return dt.strftime("%Y-%m-%d %H:%M")


def cmd_backfill(args) -> None:
    from cclog.jsonl import find_transcript, read_session_tokens
    from cclog.pricing import get_session_cost_usd

    db = _db_path()
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        _console.print(f"[red]Error opening database: {e}[/red]")
        sys.exit(1)

    try:
        rows = conn.execute("SELECT id FROM sessions ORDER BY started_at").fetchall()
    except Exception as e:
        _console.print(f"[red]Query error: {e}[/red]")
        conn.close()
        sys.exit(1)

    filled = 0
    skipped = 0

    for row in rows:
        session_id = row["id"]
        short = session_id[:8]

        transcript_path = find_transcript(session_id)
        if transcript_path is None:
            _console.print(f"  skip {short}: no transcript")
            skipped += 1
            continue

        tokens = read_session_tokens(transcript_path)
        if tokens.last_msg_id is None:
            _console.print(f"  skip {short}: empty transcript")
            skipped += 1
            continue

        cost = get_session_cost_usd(
            tokens.model,
            tokens.input_tokens,
            tokens.output_tokens,
            tokens.cache_creation_tokens,
            tokens.cache_read_tokens,
        )

        try:
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
                    tokens.input_tokens,
                    tokens.output_tokens,
                    tokens.cache_creation_tokens,
                    tokens.cache_read_tokens,
                    cost,
                    tokens.model,
                    session_id,
                ),
            )
            conn.commit()
        except Exception as e:
            _console.print(f"[red]  error {short}: {e}[/red]")
            skipped += 1
            continue

        total_tokens = tokens.input_tokens + tokens.output_tokens
        cost_str = f"${cost:.4f}" if cost is not None else "$?.????"
        _console.print(f"  ok   {short}: {total_tokens} tokens, {cost_str}")
        filled += 1

    conn.close()
    _console.print(f"Backfilled {filled} sessions, skipped {skipped}.")


def cmd_query(args) -> None:
    sql = args.sql
    db = _db_path()

    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        _console.print(f"[red]Error opening database: {e}[/red]")
        sys.exit(1)

    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
    except sqlite3.Error as e:
        _console.print(f"[red]SQL error: {e}[/red]")
        sys.exit(1)
    finally:
        conn.close()

    if not rows:
        _console.print("No results.")
        return

    columns = [desc[0] for desc in cur.description]
    table = Table()
    for col in columns:
        table.add_column(col)

    for row in rows:
        table.add_row(*[str(v) if v is not None else "" for v in row])

    _console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(prog="cclog", description="Claude Code Token Audit Logger")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("start", help="Start the daemon")
    sub.add_parser("stop", help="Stop the daemon")
    sub.add_parser("status", help="Show daemon status")
    sub.add_parser("dashboard", help="Open the web dashboard")
    sub.add_parser("today", help="Show today's token usage")

    p_sessions = sub.add_parser("sessions", help="List recent sessions")
    p_sessions.add_argument("--all", action="store_true", help="Show all sessions (not just last 24h)")

    p_query = sub.add_parser("query", help="Run a raw SQL query")
    p_query.add_argument("sql", help="SQL query string")

    sub.add_parser("backfill", help="Backfill JSONL token data for all sessions")

    args = parser.parse_args()

    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "dashboard": cmd_dashboard,
        "today": cmd_today,
        "sessions": cmd_sessions,
        "query": cmd_query,
        "backfill": cmd_backfill,
    }

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands[args.command](args)


if __name__ == "__main__":
    main()
