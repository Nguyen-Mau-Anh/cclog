# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install in editable mode (required before running cclog CLI)
pip install -e . --break-system-packages

# Run all tests
python3 -m pytest tests/ -v --tb=short

# Run a single test file
python3 -m pytest tests/test_hook.py -v --tb=short

# Run a single test by name
python3 -m pytest tests/test_hook.py::test_hook_sends_to_daemon_when_socket_exists -v

# Run with coverage
python3 -m pytest tests/ --cov=cclog --cov-report=term-missing

# Dev mode: daemon (BE) + Vite with hot reload (FE) in one command; Ctrl-C stops both
./dev.sh           # BE http://127.0.0.1:7331, FE http://localhost:5173 (proxies /api,/events)

# Daemon lifecycle
cclog start        # starts daemon, opens browser
cclog stop
cclog status

# Query the live DB
cclog today
cclog sessions [--all]
cclog query "SELECT * FROM sessions"

# Retention: strip request/response JSON from old events (event rows and
# token_ledger always kept, so cost history survives pruning)
cclog prune --days 30 [--dry-run] [--no-vacuum]
```

## Architecture

cclog is a **passive observer** of Claude Code sessions. It captures token usage and cost via Claude Code's hook system, without modifying Claude Code itself.

### Data flow

```
Claude Code (any session)
    │
    ▼ PreToolUse / PostToolUse hook fires
cclog/hook.py  ← reads JSON payload from stdin
    │
    ├─ daemon running? ──yes──▶ send JSON+\n over Unix socket (~/.cclog/cclog.sock)
    │                                   │
    │                                   ▼
    │                          cclog/daemon.py
    │                           ├─ writes to SQLite (under threading.Lock)
    │                           ├─ broadcasts SSE to browser clients
    │                           └─ serves HTTP at 0.0.0.0:7331
    │
    └─ daemon not running? ──▶ _write_direct() — writes SQLite directly (fallback)
```

### Module responsibilities

| Module | Role |
|--------|------|
| `cclog/db.py` | SQLite connection factory (WAL mode), schema DDL, `_migrate()` for additive schema changes |
| `cclog/hook.py` | Thin hook client: tries Unix socket, falls back to direct DB write. Always exits 0. |
| `cclog/daemon.py` | Persistent process: Unix socket listener + SQLite writer + HTTP server + SSE broadcaster |
| `cclog/tokens.py` | Three-tier token counting: `api` (from payload) → `tiktoken` (BPE estimate) → `heuristic` (len//4) |
| `cclog/pricing.py` | Static model price table (USD per 1M tokens); optional YAML override at `~/.cclog/pricing.yaml` |
| `cclog/cli.py` | argparse CLI: `start/stop/status/dashboard/today/sessions/query/backfill/prune` |
| `cclog/prune.py` | Two-tier retention: strips old event JSON blobs, keeps event rows + ledger |
| `cclog/web/dist/` | Built React dashboard (generated — never edit by hand); served by daemon |
| `web/` | Dashboard source: React 18 + Vite + TanStack Query. `cd web && npm run build` regenerates `cclog/web/dist/` (checked into git so `pip install` needs no Node) |

### Dashboard API contract (v2)

| Endpoint | Returns |
|----------|---------|
| `GET /api/sessions` | All sessions with aggregates (cost, calls, tokens, status) |
| `GET /api/sessions/<id>` | Session metadata + stats + `tool_breakdown` (SQL-aggregated). **No events.** |
| `GET /api/sessions/<id>/events?since=&before=&before_id=&limit=` | Paginated events, newest first. `since` = inclusive lower bound (ms); `(before, before_id)` = keyset cursor for "load older" (both required together to avoid skipping events that share a millisecond); `limit` default 200, max 1000. Returns `{events, has_more}`. |
| `GET /api/events/<id>` | Lazy request/response JSON for one event |

SSE (`/events`) broadcasts `{"type": "event", "session_id", "event": {...}}` per
tool event — the embedded summary (id, phase, tool_name, occurred_at, token/cost
fields, `null` for pre-phase) lets clients append without refetching. JSONL token
updates still broadcast `{"type": "session_update", ...}`. This is a breaking
change from the old all-`session_update` contract.

### Three-table schema

```sql
sessions    (id, started_at, model, cwd, name)
events      (id, session_id→sessions, phase, tool_name, occurred_at, input_json, output_json)
token_ledger(event_id→events PK, gross_input, gross_output, net_input, cost_usd, counted_by, model)
```

`counted_by` is always set — it identifies which tier produced the count (`"api"`, `"tiktoken"`, or `"heuristic"`). Every `token_ledger` row has exactly one `events` row (1:1 via PK).

### Hook payload contract

The hook receives a JSON object from Claude Code on stdin. All fields are optional — use `.get()` everywhere:

```json
{
  "session_id": "...",
  "tool_name": "Bash",
  "tool_input": {},
  "tool_response": {},
  "usage": {"input_tokens": 4821, "output_tokens": 43},
  "model": "claude-sonnet-4-6",
  "cwd": "/dev/myproject",
  "session_name": "optional"   ← set via CLAUDE_SESSION_NAME env var
}
```

`session_name` also read from `CLAUDE_SESSION_NAME` env var. Once set, it is never overwritten (`WHERE name IS NULL`).

### Daemon threading model

- **Unix socket listener**: background thread, one thread per connection
- **SQLite writes**: single `threading.Lock` (`_db_lock`) wraps ALL reads and writes
- **SSE clients**: separate `threading.Lock` (`_sse_lock`); lock released before any network I/O
- **HTTP server**: `ThreadingMixIn` — each request gets its own thread; SSE connections hold a thread for their lifetime

### Testing conventions

- All tests use in-memory SQLite via the `mem_conn` fixture in `conftest.py`
- `mem_conn` has `PRAGMA foreign_keys=ON` — FK violations raise `IntegrityError`
- Daemon tests use `tmp_path` for socket/DB paths and `_free_port()` to avoid conflicts
- Hook tests set `CCLOG_SOCK` to a non-existent path to force the direct-write fallback
- 3 tiktoken tests always skip (tiktoken not installed) — this is expected

### Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `CCLOG_DB` | `~/.cclog/audit.db` | SQLite database path |
| `CCLOG_SOCK` | `~/.cclog/cclog.sock` | Unix socket path |
| `CCLOG_HOST` | `0.0.0.0` | Daemon bind address |
| `CCLOG_PORT` | `7331` | Daemon HTTP port |
| `CCLOG_SESSION_TIMEOUT_MINUTES` | `30` | Inactivity before session marked closed |
| `CCLOG_IDLE_MINUTES` | `5` | Inactivity before session marked idle |
| `CCLOG_RETENTION_DAYS` | — | If set (>0), daemon strips event request/response JSON older than N days, daily. Also the default for `cclog prune --days`. The daemon only VACUUMs when a prune frees ≥50 MB (VACUUM rewrites the whole file under the DB lock); freed pages are reused by SQLite either way. Invalid values are ignored. |
| `CLAUDE_SESSION_NAME` | — | Human-readable session name (set per-project) |

### Schema migrations

New columns go through `_migrate()` in `db.py`, which uses `ALTER TABLE ... ADD COLUMN` wrapped in `try/except OperationalError`. Call `_migrate(conn)` from `setup_schema()` after the initial DDL. Never drop or rename columns.

### Process rules

- Every implementation or bug fix must be followed by a **code review agent** and a **test agent** before closing.
- If either finds issues: fix → re-review → re-test. No exceptions.
