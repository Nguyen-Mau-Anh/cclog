# Web Dashboard Performance Rewrite — Design

**Date:** 2026-06-10
**Status:** Approved (Approach A — React + Vite SPA with server-side pagination)

## Problem

The dashboard degrades with real-time data volume. Root causes:

1. **Double full-refresh loop** — a 1-second `setInterval` refetches `/api/sessions`
   and the full session detail, *and* every SSE message triggers the same full
   refetch. Two stacked refresh mechanisms, each doing full reloads.
2. **Unpaginated events API** — `/api/sessions/<id>` returns every event of a
   session with no `LIMIT`. The time-range dropdown filters client-side after
   the full payload has already been queried and transferred.
3. **Full `innerHTML` rebuilds** — `renderDetail()` destroys and recreates the
   entire DOM every second, causing scroll jumps, lost expand state, stale
   closures, and GC pressure (the last 5 commits are all scroll-restore
   band-aids for this).

Reported symptoms: jank while open, slow load of large sessions, scroll/state
jumping, browser memory growth.

## Goals

- Smooth UI with thousands of events per session and multiple live sessions.
- Server-side time-window filtering and backwards pagination of events.
- Incremental real-time updates (append one row) instead of refetch-the-world.
- Preserve all business logic: session status derivation, cost display rules
  (`jsonl_cost_usd` preferred over `estimated_cost`), tool breakdown, lazy
  event JSON, session naming, mobile layout, dark theme.

## Non-goals

- No schema changes beyond what exists. No auth. No virtualized scrolling
  (windowed fetching makes it unnecessary at limit≤500).

## Architecture

### Backend (cclog/daemon.py)

API v2 — same HTTP server, new contract:

| Endpoint | Change |
|---|---|
| `GET /api/sessions` | Unchanged (aggregate list). |
| `GET /api/sessions/<id>` | Returns metadata + stats + `tool_breakdown` aggregated in SQL (`GROUP BY tool_name`). **No `events` array.** |
| `GET /api/sessions/<id>/events?since=&before=&limit=` | **New.** Events ordered by `occurred_at DESC, id DESC`. `since` = inclusive lower bound (ms), `before` = exclusive upper bound (ms) for "load older" paging, `limit` default 200, max 1000. Returns `{"events": [...], "has_more": bool}` (fetch limit+1 to detect more). Served by the existing `(session_id, occurred_at)` composite index. |
| `GET /api/events/<id>` | Unchanged (lazy request/response JSON). |
| `GET /` and `/assets/*` | Serve built SPA from `cclog/web/dist/` (traversal-safe), fallback to minimal HTML if dist missing. |

SSE — `_write_to_db()` returns the inserted event row (id + ledger numbers);
broadcast becomes:

```json
{"type": "event", "session_id": "...",
 "event": {"id": 1, "phase": "post", "tool_name": "Bash", "occurred_at": 123,
            "gross_input": 0, "gross_output": 0, "cost_usd": 0.0, "counted_by": "api"}}
```

`jsonl_token_update` keeps emitting `{"type": "session_update", ...}`.

### Frontend (new `web/` Vite project → builds to `cclog/web/dist/`)

- **Stack:** React 18, Vite, TanStack Query v5. No Redux — state is ~90%
  server cache; UI state (selection, filters, expanded rows) is `useState`.
- **Server state (TanStack Query):**
  - `['sessions']` — list; no fast polling; 30s background `refetchInterval`
    (only for status transitions active→idle→closed), `refetchOnWindowFocus`.
  - `['session', id]` — stats + tool breakdown.
  - `['events', id, windowMs]` — `useInfiniteQuery`; pages keyed by `before`
    cursor; window select maps to `since`.
  - `['eventJson', evId]` — lazy, `staleTime: Infinity` (bounded by query GC,
    fixing the unbounded `eventJsonCache` Map).
- **Real-time flow:** one `EventSource('/events')`. On `type:"event"`:
  append the event into the head page of `['events', id, windowMs]` via
  `setQueryData` (no refetch), and invalidate `['sessions']` +
  `['session', id]` **throttled to at most once per 2s**. When
  `document.hidden`, defer invalidations until visible.
- **Components:** `App` → `Header` (search, status filter), `SessionList`,
  `SessionDetail` (`StatsCards`, `ToolBreakdown`, `EventsSection`),
  `EventsTable` (range select + Load older + expandable `EventJson` rows).
- **Timestamps:** single 10s ticker re-renders relative times (replaces the 1s
  global `querySelectorAll` walk).
- **Styling:** existing dark-theme CSS ported nearly verbatim to `src/styles.css`.

### Build & packaging

- `web/` (source) at repo root; `vite build` outputs `cclog/web/dist/`
  (checked into git so `pip install` needs no Node).
- `vite dev` proxies `/api` and `/events` to `127.0.0.1:7331`.
- `pyproject.toml` gains `[tool.setuptools.package-data] cclog = ["web/dist/**"]`
  + `include-package-data`.
- Old `cclog/web/index.html` and `cclog/web/app.js` are deleted.

## Error handling

- Events endpoint: invalid `since/before/limit` → clamped/defaulted, never 500.
- Unknown session id → 404 (unchanged).
- SSE drop → EventSource auto-reconnects; on reconnect, invalidate all queries
  (catch up on missed events); disconnected banner shown meanwhile.
- Fetch failures in UI → query `error` states render inline messages; stale
  data stays visible (no blank flash).

## Testing

- **Python (pytest):** new tests for events pagination (limit, since, before,
  has_more, ordering), session detail shape (no events, breakdown correct),
  SSE event payload shape, static dist serving incl. traversal rejection.
- **Manual/browser smoke:** seeded daemon with a high-volume synthetic session;
  verify load time, live append, window switching, load-older, expand state
  and scroll stability across updates.
- Per CLAUDE.md: code-review agent + test agent before closing.
