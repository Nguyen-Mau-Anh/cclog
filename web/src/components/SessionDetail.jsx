import { useQuery } from '@tanstack/react-query';
import { fetchSessionDetail } from '../api';
import {
  cacheTokens, displayCost, displayTotalTokens, formatCost, formatTime,
  formatTokens, relativeTime, sessionLabel,
} from '../utils';
import { useNow } from '../hooks/useNow';
import { EventsTable } from './EventsTable';

function StatsCards({ session }) {
  const total = displayTotalTokens(session);
  const cache = cacheTokens(session);
  return (
    <div className="stats-row">
      <div className="stat-card">
        <div className="stat-label">Total cost</div>
        <div className="stat-value">{formatCost(displayCost(session))}</div>
      </div>
      <div className="stat-card">
        <div className="stat-label">Tool calls</div>
        <div className="stat-value">{session.tool_calls || 0}</div>
      </div>
      <div className="stat-card" title={`${total.toLocaleString()} tokens`}>
        <div className="stat-label">Total tokens</div>
        <div className="stat-value">{formatTokens(total)}</div>
      </div>
      {cache > 0 && (
        <div className="stat-card" title={`${cache.toLocaleString()} cache tokens`}>
          <div className="stat-label">Cache tokens</div>
          <div className="stat-value">{formatTokens(cache)}</div>
        </div>
      )}
    </div>
  );
}

function ToolBreakdown({ breakdown }) {
  if (!breakdown || breakdown.length === 0) {
    return <div className="no-tools-msg">No tool events recorded.</div>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Tool</th>
          <th className="num">Calls</th>
          <th className="num">Tokens</th>
          <th className="num">Cost</th>
        </tr>
      </thead>
      <tbody>
        {breakdown.map((t) => (
          <tr key={t.tool_name}>
            <td>{t.tool_name}</td>
            <td className="num">{t.calls}</td>
            <td className="num">{formatTokens(t.tokens)}</td>
            <td className="num">{formatCost(t.cost_usd)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function SessionDetail({ sessionId, onBack }) {
  const now = useNow();
  const { data: session, isLoading, isError, error } = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => fetchSessionDetail(sessionId),
    // SSE invalidation covers new events; this slow interval covers status
    // transitions (active → idle → closed) that happen without any event.
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return <div className="detail-placeholder"><div>Loading…</div></div>;
  }
  if (isError) {
    return (
      <div className="detail-placeholder">
        <div className="error">Failed to load session: {String(error)}</div>
      </div>
    );
  }

  return (
    <>
      <div className="detail-header">
        <button id="back-btn" type="button" onClick={onBack}>← Back</button>
        <div className="detail-title-row">
          <div className="detail-title">{sessionLabel(session)}</div>
          <span className={`status-badge ${session.status || 'closed'}`}>
            {session.status || 'closed'}
          </span>
        </div>
        {session.cwd && <div className="detail-cwd">{session.cwd}</div>}
        <div className="detail-meta-grid">
          <span className="meta-label">Session ID</span>
          <span className="meta-value monospace">{session.id || '—'}</span>
          <span className="meta-label">Started</span>
          <span className="meta-value">{formatTime(session.started_at)}</span>
          <span className="meta-label">Last active</span>
          <span className="meta-value">{relativeTime(session.last_active, now)}</span>
        </div>
      </div>

      <StatsCards session={session} />

      <div className="tool-section">
        <div className="section-title">Tool breakdown</div>
        <ToolBreakdown breakdown={session.tool_breakdown} />
      </div>

      <EventsTable sessionId={sessionId} />
    </>
  );
}
