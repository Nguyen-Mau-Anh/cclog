'use strict';

// ── State ─────────────────────────────────────────────────────────────────

let sessions = [];
let selectedSessionId = null;
let searchQuery = '';
let statusFilter = 'all'; // 'all' | 'active' | 'active_idle'
let eventsTimeRange = 3_600_000; // ms; 0 = all time

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

function sessionLabel(s) {
    const base = (s.cwd || '').split('/').filter(Boolean).pop() || s.id.slice(0, 8);
    return s.name || base;
}

function sessionSublabel(s) {
    return s.name ? s.cwd : null;  // only show full path if there's a custom name
}

function isMobile() {
    return window.matchMedia('(max-width: 699px)').matches;
}

function updateAllTimestamps() {
    document.querySelectorAll('[data-timestamp]').forEach(el => {
        const ms = parseInt(el.dataset.timestamp, 10);
        if (!isNaN(ms)) {
            el.textContent = relativeTime(ms);
        }
    });
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
        const label = sessionLabel(s);
        const sublabel = sessionSublabel(s);
        const cost = formatCost((s.jsonl_cost_usd != null && s.jsonl_cost_usd > 0) ? s.jsonl_cost_usd : s.estimated_cost);
        const calls = s.tool_calls || 0;
        const sublabelHtml = sublabel
            ? `<div class="session-meta" style="font-size:10px;color:var(--text-dim)">${escapeHtml(sublabel)}</div>`
            : '';
        return `
        <div class="session-item ${isSelected ? 'selected' : ''}" data-id="${escapeAttr(s.id)}">
            <span class="session-dot ${s.status}">${statusDot(s.status)}</span>
            <div class="session-info">
                <div class="session-cwd" title="${escapeAttr(cwd)}">${escapeHtml(label)}</div>
                ${sublabelHtml}
                <div class="session-meta">${escapeHtml(cost)} · ${calls} call${calls !== 1 ? 's' : ''} · <span class="rel-time" data-timestamp="${s.last_active || ''}">${relativeTime(s.last_active)}</span></div>
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

    // Mobile: switch to detail view
    if (isMobile()) {
        document.querySelector('.content').classList.add('mobile-detail-active');
    }

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
    const rawEventsOpen = detail.querySelector('details')?.open ?? false;
    const scrollTop = detail.scrollTop;

    // Save which event JSON rows are currently expanded
    const expandedEvIds = new Set();
    detail.querySelectorAll('.ev-json-row').forEach(row => {
        if (row.style.display !== 'none') expandedEvIds.add(row.id);
    });

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

    // ── Raw events (filtered by time range) ──
    const cutoff = eventsTimeRange ? Date.now() - eventsTimeRange : 0;
    const rawEvents = events.filter(ev => !eventsTimeRange || (ev.occurred_at || 0) >= cutoff).reverse();
    const rawRows = rawEvents.map((ev, i) => {
        const tokens = (ev.gross_input || 0) + (ev.gross_output || 0);
        const hasJson = ev.input_json != null || ev.output_json != null;
        const expandId = `ev-json-${i}`;
        return `
            <tr class="ev-row${hasJson ? ' ev-expandable' : ''}" data-ev-idx="${i}">
                <td>
                    ${hasJson ? `<span class="ev-toggle" data-target="${expandId}" title="Show request/response">▶</span> ` : ''}
                    ${escapeHtml(ev.tool_name || '—')}
                </td>
                <td><span class="phase-${ev.phase || ''}">${escapeHtml(ev.phase || '—')}</span></td>
                <td class="num">${formatTokens(tokens)}</td>
                <td class="num">${formatCost(ev.cost_usd)}</td>
                <td class="monospace" style="color:var(--text-muted);font-size:11px">${escapeHtml(relativeTime(ev.occurred_at))}</td>
            </tr>
            ${hasJson ? `<tr class="ev-json-row" id="${expandId}" style="display:none">
                <td colspan="5" style="padding:0">
                    <div class="ev-json-panel">
                        ${ev.input_json != null ? `<div class="ev-json-block"><div class="ev-json-label">Request</div><pre class="ev-json-pre">${escapeHtml(JSON.stringify(ev.input_json, null, 2))}</pre></div>` : ''}
                        ${ev.output_json != null ? `<div class="ev-json-block"><div class="ev-json-label">Response</div><pre class="ev-json-pre">${escapeHtml(JSON.stringify(ev.output_json, null, 2))}</pre></div>` : ''}
                    </div>
                </td>
            </tr>` : ''}
        `;
    }).join('');

    const statusBadge = `<span class="status-badge ${session.status || 'closed'}">${session.status || 'closed'}</span>`;
    const titleLabel = sessionLabel(session);
    const cwdPath = session.cwd || '';
    const cwdSubline = cwdPath
        ? `<div style="font-size:12px;color:var(--text-muted);margin-bottom:6px;font-family:monospace">${escapeHtml(cwdPath)}</div>`
        : '';

    detail.innerHTML = `
        <div class="detail-header">
            <button id="back-btn" type="button">&#8592; Back</button>
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
                <div class="detail-title" style="margin-bottom:0">${escapeHtml(titleLabel)}</div>
                ${statusBadge}
            </div>
            ${cwdSubline}
            <div class="detail-meta-grid">
                <span class="meta-label">Session ID</span>
                <span class="meta-value monospace">${escapeHtml(session.id || '—')}</span>

                <span class="meta-label">Started</span>
                <span class="meta-value">${escapeHtml(formatTime(session.started_at))}</span>

                <span class="meta-label">Last active</span>
                <span class="meta-value"><span class="rel-time" data-timestamp="${session.last_active || ''}">${relativeTime(session.last_active)}</span></span>
            </div>
        </div>

        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-label">Total cost</div>
                <div class="stat-value">${formatCost((session.jsonl_cost_usd != null && session.jsonl_cost_usd > 0) ? session.jsonl_cost_usd : session.estimated_cost)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Tool calls</div>
                <div class="stat-value">${session.tool_calls || 0}</div>
            </div>
            ${(() => { const t = ((session.jsonl_input_tokens || 0) + (session.jsonl_output_tokens || 0)) > 0 ? (session.jsonl_input_tokens || 0) + (session.jsonl_output_tokens || 0) : (session.total_tokens || 0); return `<div class="stat-card" title="${t.toLocaleString()} tokens"><div class="stat-label">Total tokens</div><div class="stat-value">${formatTokens(t)}</div></div>`; })()}
            ${(() => { const c = (session.jsonl_cache_creation_tokens || 0) + (session.jsonl_cache_read_tokens || 0); return c > 0 ? `<div class="stat-card" title="${c.toLocaleString()} cache tokens"><div class="stat-label">Cache tokens</div><div class="stat-value">${formatTokens(c)}</div></div>` : ''; })()}
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
                <summary>
                    Raw events (${rawEvents.length})
                    <select id="events-range-select" style="margin-left:8px;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:2px 6px;font-size:12px;cursor:pointer">
                        <option value="3600000">Last 1h</option>
                        <option value="21600000">Last 6h</option>
                        <option value="86400000">Last 24h</option>
                        <option value="0">All time</option>
                    </select>
                </summary>
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

    // Restore <details> open state (lost when innerHTML is replaced)
    if (rawEventsOpen) {
        const detailsEl = detail.querySelector('details');
        if (detailsEl) detailsEl.open = true;
    }

    // Restore and wire up events range selector
    const rangeSelect = document.getElementById('events-range-select');
    if (rangeSelect) {
        rangeSelect.value = String(eventsTimeRange);
        rangeSelect.addEventListener('change', (e) => {
            e.stopPropagation(); // don't toggle <details>
            eventsTimeRange = parseInt(e.target.value, 10);
        });
    }

    // Wire up event JSON toggles and restore previously expanded rows
    detail.querySelectorAll('.ev-toggle').forEach(btn => {
        const targetId = btn.dataset.target;
        const row = document.getElementById(targetId);
        if (row && expandedEvIds.has(targetId)) {
            row.style.display = 'table-row';
            btn.textContent = '▼';
        }
        btn.addEventListener('click', () => {
            if (!row) return;
            const open = row.style.display !== 'none';
            row.style.display = open ? 'none' : 'table-row';
            btn.textContent = open ? '▶' : '▼';
        });
    });

    // Wire up back button (mobile)
    const backBtn = document.getElementById('back-btn');
    if (backBtn) {
        backBtn.addEventListener('click', () => {
            document.querySelector('.content').classList.remove('mobile-detail-active');
            selectedSessionId = null;
            renderSessionList();
        });
    }

    // Restore scroll position after re-render
    detail.scrollTop = scrollTop;
}

// ── Silent detail refresh (no loading flash) ──────────────────────────────

async function refreshDetailSilent(id) {
    // Skip re-render while user is interacting with any dropdown in the detail panel
    if (document.activeElement && document.activeElement.closest('#detail-panel')) return;
    // Re-fetch and re-render detail WITHOUT clearing the panel first
    try {
        const resp = await fetch(`/api/sessions/${encodeURIComponent(id)}`);
        if (!resp.ok) return;
        const data = await resp.json();
        renderDetail(data);
    } catch (err) {
        // silently ignore — panel keeps showing last known data
    }
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
                    refreshDetailSilent(data.session_id);
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
setInterval(updateAllTimestamps, 1_000);
setInterval(async () => {
    await fetchSessions();
    if (selectedSessionId) {
        refreshDetailSilent(selectedSessionId);
    }
}, 1_000);
