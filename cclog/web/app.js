'use strict';

// ── State ─────────────────────────────────────────────────────────────────

let sessions = [];
let selectedSessionId = null;
let searchQuery = '';
let statusFilter = 'all'; // 'all' | 'active' | 'active_idle'

// ── Helper functions ──────────────────────────────────────────────────────

function formatCost(usd) {
    if (usd == null) return '$0.00';
    return usd < 0.01 ? '<$0.01' : `$${usd.toFixed(2)}`;
}

function relativeTime(ms) {
    if (!ms) return 'never';
    const diff = Date.now() - ms;
    if (diff < 60_000) return 'just now';
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} min ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return `${Math.floor(diff / 86_400_000)}d ago`;
}

function formatTime(ms) {
    if (!ms) return '—';
    return new Date(ms).toLocaleString(undefined, {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit',
    });
}

function formatTokens(n) {
    if (!n) return '0';
    return n.toLocaleString();
}

function statusDot(status) {
    if (status === 'active') return '●';
    if (status === 'idle')   return '●';
    return '○';
}

function cwdDisplay(cwd) {
    return cwd || 'unknown';
}

// ── Session list rendering ────────────────────────────────────────────────

function filteredSessions() {
    return sessions.filter(s => {
        // Status filter
        if (statusFilter === 'active' && s.status !== 'active') return false;
        if (statusFilter === 'active_idle' && s.status === 'closed') return false;

        // Search filter
        if (searchQuery) {
            const cwd = (s.cwd || '').toLowerCase();
            if (!cwd.includes(searchQuery)) return false;
        }

        return true;
    });
}

function renderSessionList() {
    const list = document.getElementById('session-list');
    const visible = filteredSessions();

    // Update active count in header (always from full sessions array)
    const activeCount = sessions.filter(s => s.status === 'active').length;
    document.getElementById('active-count-num').textContent = activeCount;

    if (visible.length === 0) {
        list.innerHTML = '<div class="empty-list">No sessions match your filter.</div>';
        return;
    }

    list.innerHTML = visible.map(s => {
        const isSelected = s.id === selectedSessionId;
        const cwd = cwdDisplay(s.cwd);
        const shortCwd = cwd.length > 32 ? '…' + cwd.slice(-30) : cwd;
        const cost = formatCost(s.estimated_cost);
        const calls = s.tool_calls || 0;
        return `
        <div class="session-item ${isSelected ? 'selected' : ''}" data-id="${escapeAttr(s.id)}">
            <span class="session-dot ${s.status}">${statusDot(s.status)}</span>
            <div class="session-info">
                <div class="session-cwd" title="${escapeAttr(cwd)}">${escapeHtml(shortCwd)}</div>
                <div class="session-meta">${escapeHtml(cost)} · ${calls} call${calls !== 1 ? 's' : ''}</div>
            </div>
        </div>`;
    }).join('');

    // Attach click handlers
    list.querySelectorAll('.session-item').forEach(el => {
        el.addEventListener('click', () => {
            const id = el.dataset.id;
            selectSession(id);
        });
    });
}

// ── Session detail rendering ──────────────────────────────────────────────

async function selectSession(id) {
    selectedSessionId = id;
    renderSessionList(); // update selection highlight

    const detail = document.getElementById('detail-panel');
    detail.innerHTML = '<div class="detail-placeholder"><div>Loading…</div></div>';

    try {
        const resp = await fetch(`/api/sessions/${encodeURIComponent(id)}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        renderDetail(data);
    } catch (err) {
        detail.innerHTML = `<div class="detail-placeholder"><div style="color:var(--danger)">Failed to load session: ${escapeHtml(String(err))}</div></div>`;
    }
}

function renderDetail(session) {
    const detail = document.getElementById('detail-panel');

    // ── Tool breakdown ──
    const toolMap = new Map();
    const events = session.events || [];
    for (const ev of events) {
        const name = ev.tool_name || 'unknown';
        if (!toolMap.has(name)) {
            toolMap.set(name, { calls: 0, tokens: 0, cost: 0 });
        }
        const t = toolMap.get(name);
        t.calls += 1;
        t.tokens += (ev.gross_input || 0) + (ev.gross_output || 0);
        t.cost += ev.cost_usd || 0;
    }

    const toolRows = [...toolMap.entries()]
        .sort((a, b) => b[1].cost - a[1].cost)
        .map(([name, stats]) => `
            <tr>
                <td>${escapeHtml(name)}</td>
                <td class="num">${stats.calls}</td>
                <td class="num">${formatTokens(stats.tokens)}</td>
                <td class="num">${formatCost(stats.cost)}</td>
            </tr>
        `).join('');

    // ── Raw events (last 20) ──
    const rawEvents = events.slice(-20).reverse();
    const rawRows = rawEvents.map(ev => {
        const tokens = (ev.gross_input || 0) + (ev.gross_output || 0);
        return `
            <tr>
                <td>${escapeHtml(ev.tool_name || '—')}</td>
                <td><span class="phase-${ev.phase || ''}">${escapeHtml(ev.phase || '—')}</span></td>
                <td class="num">${formatTokens(tokens)}</td>
                <td class="num">${formatCost(ev.cost_usd)}</td>
                <td class="monospace" style="color:var(--text-muted);font-size:11px">${escapeHtml(relativeTime(ev.occurred_at))}</td>
            </tr>
        `;
    }).join('');

    const statusBadge = `<span class="status-badge ${session.status || 'closed'}">${session.status || 'closed'}</span>`;

    detail.innerHTML = `
        <div class="detail-header">
            <div class="detail-title">${escapeHtml(cwdDisplay(session.cwd))}</div>
            <div class="detail-meta-grid">
                <span class="meta-label">Status</span>
                <span>${statusBadge}</span>

                <span class="meta-label">Session ID</span>
                <span class="meta-value monospace">${escapeHtml(session.id || '—')}</span>

                <span class="meta-label">Started</span>
                <span class="meta-value">${escapeHtml(formatTime(session.started_at))}</span>

                <span class="meta-label">Last active</span>
                <span class="meta-value">${escapeHtml(relativeTime(session.last_active))}</span>
            </div>
        </div>

        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-label">Total cost</div>
                <div class="stat-value">${formatCost(session.estimated_cost)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Tool calls</div>
                <div class="stat-value">${session.tool_calls || 0}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total tokens</div>
                <div class="stat-value">${formatTokens(session.total_tokens)}</div>
            </div>
        </div>

        <div class="tool-section">
            <div class="section-title">Tool breakdown</div>
            ${toolRows ? `
            <table>
                <thead>
                    <tr>
                        <th>Tool</th>
                        <th class="num">Calls</th>
                        <th class="num">Tokens</th>
                        <th class="num">Cost</th>
                    </tr>
                </thead>
                <tbody>${toolRows}</tbody>
            </table>` : '<div style="color:var(--text-dim);font-size:13px">No tool events recorded.</div>'}
        </div>

        <div class="raw-events-section">
            <details>
                <summary>Raw events (last ${rawEvents.length})</summary>
                <div class="raw-events-table">
                    ${rawRows ? `
                    <table>
                        <thead>
                            <tr>
                                <th>Tool</th>
                                <th>Phase</th>
                                <th class="num">Tokens</th>
                                <th class="num">Cost</th>
                                <th>Time</th>
                            </tr>
                        </thead>
                        <tbody>${rawRows}</tbody>
                    </table>` : '<div style="padding:12px;color:var(--text-dim)">No events.</div>'}
                </div>
            </details>
        </div>
    `;
}

// ── API fetching ──────────────────────────────────────────────────────────

async function fetchSessions() {
    try {
        const resp = await fetch('/api/sessions');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        sessions = await resp.json();
    } catch (err) {
        console.error('Failed to fetch sessions:', err);
    }
    renderSessionList();
}

// ── SSE ───────────────────────────────────────────────────────────────────

function connectSSE() {
    const banner = document.getElementById('disconnected-banner');
    const evtSource = new EventSource('/events');

    evtSource.onopen = () => {
        banner.classList.remove('visible');
    };

    evtSource.onmessage = async (e) => {
        try {
            const data = JSON.parse(e.data);
            if (data.type === 'session_update') {
                await fetchSessions();
                // If detail panel is showing this session, refresh it
                if (selectedSessionId === data.session_id) {
                    selectSession(data.session_id);
                }
            }
        } catch (err) {
            console.error('SSE parse error:', err);
        }
    };

    evtSource.onerror = () => {
        banner.classList.add('visible');
        // EventSource auto-reconnects; banner goes away on next onopen
    };
}

// ── Controls ──────────────────────────────────────────────────────────────

document.getElementById('search-input').addEventListener('input', (e) => {
    searchQuery = e.target.value.trim().toLowerCase();
    renderSessionList();
});

document.getElementById('status-filter').addEventListener('change', (e) => {
    statusFilter = e.target.value;
    renderSessionList();
});

// ── Escape helpers ────────────────────────────────────────────────────────

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeAttr(str) {
    return String(str).replace(/"/g, '&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────

fetchSessions();
connectSSE();
