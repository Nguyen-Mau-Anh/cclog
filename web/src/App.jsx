import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchSessions } from './api';
import { useSSE } from './hooks/useSSE';
import { SessionList } from './components/SessionList';
import { SessionDetail } from './components/SessionDetail';

export default function App() {
  const [selectedId, setSelectedId] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [mobileDetail, setMobileDetail] = useState(false);
  const [connected, setConnected] = useState(true);

  useSSE(setConnected);

  // SSE drives freshness; the slow 30s interval only catches status
  // transitions (active → idle → closed) that happen without new events.
  const { data: sessions = [] } = useQuery({
    queryKey: ['sessions'],
    queryFn: fetchSessions,
    refetchInterval: 30_000,
  });

  const activeCount = sessions.filter((s) => s.status === 'active').length;

  function handleSelect(id) {
    setSelectedId(id);
    setMobileDetail(true);
  }

  function handleBack() {
    setSelectedId(null);
    setMobileDetail(false);
  }

  return (
    <div className="app">
      <header>
        <span className="logo">cclog</span>
        <span className="active-count">
          <span className="dot">●</span> {activeCount} active sessions
        </span>
        <div className="header-controls">
          <input
            type="search"
            placeholder="search by path…"
            autoComplete="off"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="active">Active only</option>
            <option value="active_idle">Idle + Active</option>
          </select>
        </div>
      </header>

      {!connected && (
        <div id="disconnected-banner">Disconnected — reconnecting…</div>
      )}

      <div className={`content ${mobileDetail ? 'mobile-detail-active' : ''}`}>
        <aside className="sessions-panel">
          <div className="sessions-panel-header">Sessions</div>
          <div id="session-list">
            <SessionList
              sessions={sessions}
              selectedId={selectedId}
              onSelect={handleSelect}
              searchQuery={searchQuery}
              statusFilter={statusFilter}
            />
          </div>
        </aside>

        <main className="detail-panel">
          {selectedId ? (
            <SessionDetail key={selectedId} sessionId={selectedId} onBack={handleBack} />
          ) : (
            <div className="detail-placeholder">
              <div className="big-icon">◫</div>
              <div>Select a session to see details</div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
