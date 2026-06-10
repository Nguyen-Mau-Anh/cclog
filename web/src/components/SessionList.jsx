import { memo } from 'react';
import {
  displayCost, formatCost, relativeTime, sessionLabel, sessionSublabel, statusDot,
} from '../utils';
import { useNow } from '../hooks/useNow';

const SessionItem = memo(function SessionItem({ session, isSelected, onSelect, now }) {
  const calls = session.tool_calls || 0;
  const sublabel = sessionSublabel(session);
  return (
    <div
      className={`session-item ${isSelected ? 'selected' : ''}`}
      onClick={() => onSelect(session.id)}
    >
      <span className={`session-dot ${session.status}`}>{statusDot(session.status)}</span>
      <div className="session-info">
        <div className="session-cwd" title={session.cwd || 'unknown'}>
          {sessionLabel(session)}
        </div>
        {sublabel && <div className="session-sublabel">{sublabel}</div>}
        <div className="session-meta">
          {formatCost(displayCost(session))} · {calls} call{calls !== 1 ? 's' : ''} ·{' '}
          {relativeTime(session.last_active, now)}
        </div>
      </div>
    </div>
  );
});

export function SessionList({ sessions, selectedId, onSelect, searchQuery, statusFilter }) {
  const now = useNow();

  const query = searchQuery.trim().toLowerCase();
  const visible = sessions.filter((s) => {
    if (statusFilter === 'active' && s.status !== 'active') return false;
    if (statusFilter === 'active_idle' && s.status === 'closed') return false;
    if (query) {
      const cwd = (s.cwd || '').toLowerCase();
      if (!cwd.includes(query)) return false;
    }
    return true;
  });

  if (visible.length === 0) {
    // Distinguish "nothing logged yet" (first run) from "filtered everything out"
    if (sessions.length === 0) {
      return (
        <div className="empty-list">
          No sessions yet. Sessions appear here once Claude Code hooks start
          sending events.
        </div>
      );
    }
    return <div className="empty-list">No sessions match your filter.</div>;
  }

  return (
    <>
      {visible.map((s) => (
        <SessionItem
          key={s.id}
          session={s}
          isSelected={s.id === selectedId}
          onSelect={onSelect}
          now={now}
        />
      ))}
    </>
  );
}
