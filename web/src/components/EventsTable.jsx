import { Fragment, useMemo, useState } from 'react';
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query';
import { fetchSessionEvents } from '../api';
import { formatCost, formatTime, formatTokens, relativeTime } from '../utils';
import { useNow } from '../hooks/useNow';
import { EventJson } from './EventJson';

const WINDOW_OPTIONS = [
  { value: 3_600_000, label: 'Last 1h' },
  { value: 21_600_000, label: 'Last 6h' },
  { value: 86_400_000, label: 'Last 24h' },
  { value: 0, label: 'All time' },
];

const PAGE_SIZE = 200;

function EventRow({ event, isExpanded, onToggle, now }) {
  const tokens = (event.gross_input || 0) + (event.gross_output || 0);
  return (
    <Fragment>
      <tr className="ev-row ev-expandable">
        <td>
          <span
            className="ev-toggle"
            title="Show request/response"
            onClick={() => onToggle(event.id)}
          >
            {isExpanded ? '▼' : '▶'}
          </span>{' '}
          {event.tool_name || '—'}
        </td>
        <td><span className={`phase-${event.phase || ''}`}>{event.phase || '—'}</span></td>
        <td className="num">{formatTokens(tokens)}</td>
        <td className="num">{event.cost_usd == null ? '—' : formatCost(event.cost_usd)}</td>
        <td className="monospace ev-time" title={formatTime(event.occurred_at)}>
          {relativeTime(event.occurred_at, now)}
        </td>
      </tr>
      {isExpanded && (
        <tr className="ev-json-row">
          <td colSpan={5} style={{ padding: 0 }}>
            <EventJson eventId={event.id} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function EventsTable({ sessionId }) {
  const [windowMs, setWindowMs] = useState(3_600_000);
  // Keyed by stable event id (not row index) — expansion survives refreshes.
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const now = useNow();

  // Anchor the window's lower bound once per window selection — recomputing
  // Date.now() per page fetch would make pages of one query use different
  // bounds (drifting window). Live events still arrive via SSE append.
  const since = useMemo(
    () => (windowMs ? Date.now() - windowMs : 0),
    [windowMs, sessionId],
  );

  const {
    data, isLoading, isError, error, fetchNextPage, hasNextPage, isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['events', sessionId, windowMs, since],
    queryFn: ({ pageParam }) =>
      fetchSessionEvents(sessionId, {
        since,
        before: pageParam?.before ?? 0,
        beforeId: pageParam?.beforeId ?? 0,
        limit: PAGE_SIZE,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more || lastPage.events.length === 0) return undefined;
      const oldest = lastPage.events[lastPage.events.length - 1];
      // Composite keyset cursor — a bare timestamp would skip events that
      // share the boundary row's millisecond.
      return { before: oldest.occurred_at, beforeId: oldest.id };
    },
    // Keep the previous window's rows on screen while the new window loads —
    // no "Loading…" flash when switching 1h → 24h. Safe across sessions
    // because this component remounts per session (fresh observer).
    placeholderData: keepPreviousData,
  });

  // Auto-load the next page when the user scrolls near the bottom — walking
  // a 5000-event session 200 rows per button click is 25 clicks of friction.
  // The button stays as an explicit/accessible fallback.
  function handleScroll(e) {
    const el = e.currentTarget;
    if (
      hasNextPage && !isFetchingNextPage
      && el.scrollTop + el.clientHeight >= el.scrollHeight - 60
    ) {
      fetchNextPage();
    }
  }

  function toggleExpanded(id) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const events = data ? data.pages.flatMap((p) => p.events) : [];

  return (
    <div className="raw-events-section">
      <details>
        <summary>
          Raw events ({events.length}{hasNextPage ? '+' : ''})
          <select
            className="events-range-select"
            value={String(windowMs)}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setWindowMs(parseInt(e.target.value, 10))}
          >
            {WINDOW_OPTIONS.map((o) => (
              <option key={o.value} value={String(o.value)}>{o.label}</option>
            ))}
          </select>
        </summary>
        <div className="raw-events-table" onScroll={handleScroll}>
          {isLoading && <div className="table-msg">Loading…</div>}
          {isError && <div className="table-msg error">Failed to load events: {String(error)}</div>}
          {!isLoading && !isError && events.length === 0 && (
            <div className="table-msg">No events.</div>
          )}
          {events.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Phase</th>
                  <th className="num">Tokens</th>
                  <th className="num">Cost</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => (
                  <EventRow
                    key={ev.id}
                    event={ev}
                    isExpanded={expandedIds.has(ev.id)}
                    onToggle={toggleExpanded}
                    now={now}
                  />
                ))}
              </tbody>
            </table>
          )}
          {hasNextPage && (
            <button
              type="button"
              className="load-older-btn"
              disabled={isFetchingNextPage}
              onClick={() => fetchNextPage()}
            >
              {isFetchingNextPage ? 'Loading…' : 'Load older events'}
            </button>
          )}
        </div>
      </details>
    </div>
  );
}
