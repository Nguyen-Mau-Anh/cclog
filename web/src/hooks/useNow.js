import { useEffect, useState } from 'react';

/** Re-render every `intervalMs` so relative timestamps stay fresh.
 * Replaces the legacy 1s global DOM walk with a 10s React tick. */
export function useNow(intervalMs = 10_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
