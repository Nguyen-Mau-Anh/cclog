// Display helpers — business logic ported verbatim from the legacy app.js.

export function formatCost(usd) {
  if (usd == null) return '$0.00';
  return usd < 0.01 ? '<$0.01' : `$${usd.toFixed(2)}`;
}

export function relativeTime(ms, now = Date.now()) {
  if (!ms) return 'never';
  const diff = now - ms;
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} min ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

export function formatTime(ms) {
  if (!ms) return '—';
  return new Date(ms).toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

export function formatTokens(n) {
  if (!n) return '0';
  return n.toLocaleString();
}

export function statusDot(status) {
  return status === 'active' || status === 'idle' ? '●' : '○';
}

export function sessionLabel(s) {
  const base = (s.cwd || '').split('/').filter(Boolean).pop() || s.id.slice(0, 8);
  return s.name || base;
}

// Only show the full path as a sublabel when a custom name hides it.
export function sessionSublabel(s) {
  return s.name ? s.cwd : null;
}

// jsonl-sourced cost (accurate) wins over the hook-side estimate.
export function displayCost(s) {
  return (s.jsonl_cost_usd != null && s.jsonl_cost_usd > 0)
    ? s.jsonl_cost_usd
    : s.estimated_cost;
}

export function displayTotalTokens(s) {
  const jsonl = (s.jsonl_input_tokens || 0) + (s.jsonl_output_tokens || 0);
  return jsonl > 0 ? jsonl : (s.total_tokens || 0);
}

export function cacheTokens(s) {
  return (s.jsonl_cache_creation_tokens || 0) + (s.jsonl_cache_read_tokens || 0);
}
