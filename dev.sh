#!/usr/bin/env bash
# Start backend (cclog daemon) + frontend (Vite dev server) together.
#
#   ./dev.sh
#
# Backend:  http://127.0.0.1:${CCLOG_PORT:-7331}  (API + SSE)
# Frontend: http://127.0.0.1:5173                 (hot reload; proxies /api and /events)
#
# Ctrl-C stops both. For production use `cclog start` — the daemon alone
# serves the built dashboard from cclog/web/dist.
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c "import cclog" 2>/dev/null; then
  echo "cclog not importable — run: pip install -e . --break-system-packages" >&2
  exit 1
fi

if [ ! -d web/node_modules ]; then
  echo "Installing frontend dependencies (first run)…"
  (cd web && npm install --no-fund --no-audit)
fi

export CCLOG_HOST="${CCLOG_HOST:-127.0.0.1}"
export CCLOG_PORT="${CCLOG_PORT:-7331}"

echo "Starting daemon on ${CCLOG_HOST}:${CCLOG_PORT}…"
python3 -m cclog.daemon &
DAEMON_PID=$!

(cd web && npm run dev) &
VITE_PID=$!

# Both run as background jobs and the script blocks in `wait`, which bash
# interrupts for signals — so the trap fires immediately on Ctrl-C or kill.
# (With vite in the foreground, bash would defer the trap until vite exited.)
# Cleanup signals the whole process group: killing only the job PIDs would
# leave npm/vite grandchildren running.
CLEANED=0
cleanup() {
  [ "$CLEANED" = 1 ] && return
  CLEANED=1
  trap '' INT TERM            # ignore our own group-wide signal
  kill -TERM 0 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Exit when the frontend exits (whatever stops vite stops the stack).
wait "$VITE_PID"
