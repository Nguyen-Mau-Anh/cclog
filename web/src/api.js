async function fetchJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export function fetchSessions() {
  return fetchJson('/api/sessions');
}

export function fetchSessionDetail(id) {
  return fetchJson(`/api/sessions/${encodeURIComponent(id)}`);
}

export function fetchSessionEvents(id, { since = 0, before = 0, beforeId = 0, limit = 200 } = {}) {
  const params = new URLSearchParams();
  if (since) params.set('since', String(since));
  if (before) params.set('before', String(before));
  if (beforeId) params.set('before_id', String(beforeId));
  params.set('limit', String(limit));
  return fetchJson(`/api/sessions/${encodeURIComponent(id)}/events?${params}`);
}

export function fetchEventJson(eventId) {
  return fetchJson(`/api/events/${encodeURIComponent(eventId)}`);
}
