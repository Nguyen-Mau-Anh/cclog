"""
cclog.prune — two-tier retention for the events table.

The expensive part of the database is the raw request/response JSON on
events (debugging payload); the part worth keeping forever (token counts,
costs) lives in the tiny token_ledger. Pruning therefore strips the JSON
blobs from old events but keeps the event rows and ledger intact — cost
history and dashboard aggregates are unaffected.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

_OLD_JSON_WHERE = (
    "occurred_at < ? AND (input_json IS NOT NULL OR output_json IS NOT NULL)"
)


def retention_days_from_env() -> "int | None":
    """Parse CCLOG_RETENTION_DAYS tolerantly: unset, garbage, zero or
    negative all mean retention is off. Shared by daemon and CLI so a typo
    in the env var can never crash a command."""
    raw = os.environ.get("CCLOG_RETENTION_DAYS")
    if not raw:
        return None
    try:
        days = int(raw)
    except ValueError:
        return None
    return days if days > 0 else None


@dataclass
class PruneResult:
    events_stripped: int
    bytes_freed: int  # JSON bytes removed; file shrinks after VACUUM


def measure_prunable(conn: sqlite3.Connection, cutoff_ms: int) -> PruneResult:
    """What strip_old_event_json(cutoff_ms) would remove, without removing it."""
    row = conn.execute(
        "SELECT COUNT(*), "
        "COALESCE(SUM(LENGTH(COALESCE(input_json,'')) + LENGTH(COALESCE(output_json,''))), 0) "
        f"FROM events WHERE {_OLD_JSON_WHERE}",
        (cutoff_ms,),
    ).fetchone()
    return PruneResult(events_stripped=row[0], bytes_freed=row[1])


def strip_old_event_json(conn: sqlite3.Connection, cutoff_ms: int) -> PruneResult:
    """NULL out input_json/output_json on events older than cutoff_ms.

    Event rows and token_ledger rows are preserved. Commits. Does not VACUUM
    (caller decides — see vacuum())."""
    measured = measure_prunable(conn, cutoff_ms)
    if not measured.events_stripped:
        return measured
    cur = conn.execute(
        f"UPDATE events SET input_json = NULL, output_json = NULL WHERE {_OLD_JSON_WHERE}",
        (cutoff_ms,),
    )
    conn.commit()
    # rowcount is authoritative (rows could change between measure and update);
    # bytes_freed stays the measured estimate.
    return PruneResult(events_stripped=cur.rowcount, bytes_freed=measured.bytes_freed)


def vacuum(conn: sqlite3.Connection) -> None:
    """Reclaim file space after pruning. Must run outside a transaction."""
    conn.commit()  # end any implicit transaction
    conn.execute("VACUUM")
