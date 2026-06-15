import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';

const INVALIDATE_THROTTLE_MS = 2000;
const MAX_HEAD_EVENTS = 500;

/**
 * Single EventSource for the whole app.
 *
 * - `type:"event"` messages carry the new event row: append it directly into
 *   every cached events page for that session (no refetch).
 * - Aggregate queries (['sessions'], ['session', id]) are invalidated at most
 *   once per 2s, and not at all while the tab is hidden (flushed on return).
 */
export function useSSE(onConnectionChange) {
  const queryClient = useQueryClient();
  const stateRef = useRef({ pendingSessions: new Set(), timer: null });

  useEffect(() => {
    const state = stateRef.current;

    function flushInvalidations() {
      state.timer = null;
      if (document.hidden) return; // re-flushed by visibilitychange below
      if (state.pendingSessions.size === 0) return;
      const ids = [...state.pendingSessions];
      state.pendingSessions.clear();
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
      for (const id of ids) {
        queryClient.invalidateQueries({ queryKey: ['session', id] });
      }
    }

    function scheduleInvalidation(sessionId) {
      state.pendingSessions.add(sessionId);
      if (!state.timer) {
        state.timer = setTimeout(flushInvalidations, INVALIDATE_THROTTLE_MS);
      }
    }

    function appendEvent(sessionId, event) {
      // Update every cached events window for this session ([events, id, windowMs, since]).
      const queries = queryClient.getQueryCache().findAll({ queryKey: ['events', sessionId] });
      for (const q of queries) {
        if (q.state.data === undefined) {
          // Initial fetch still in flight — its SQL may predate this event.
          // Mark stale so it refetches once settled instead of dropping the event.
          queryClient.invalidateQueries({ queryKey: q.queryKey });
          continue;
        }
        queryClient.setQueryData(q.queryKey, (data) => {
          if (!data || !data.pages || data.pages.length === 0) return data;
          const head = data.pages[0];
          if (head.events.some((e) => e.id === event.id)) return data; // dedupe
          let events = [event, ...head.events];
          let hasMore = head.has_more;
          // Cap unbounded growth from live appends. Only safe with a single
          // page — trimming the head with older pages cached would open a gap
          // between pages; the cursor makes trimmed rows refetchable instead.
          if (data.pages.length === 1 && events.length > MAX_HEAD_EVENTS) {
            events = events.slice(0, MAX_HEAD_EVENTS);
            hasMore = true;
          }
          const newHead = { ...head, events, has_more: hasMore };
          return { ...data, pages: [newHead, ...data.pages.slice(1)] };
        });
      }
    }

    let wasDisconnected = false;
    const es = new EventSource('/events');
    es.onopen = () => {
      onConnectionChange?.(true);
      if (wasDisconnected) {
        wasDisconnected = false;
        // Catch up on everything missed while disconnected (daemon restart,
        // laptop sleep) — events, session stats, the list, all of it.
        queryClient.invalidateQueries();
      }
    };
    es.onerror = () => {
      wasDisconnected = true;
      onConnectionChange?.(false);
    };
    es.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (!msg.session_id) return;
      if (msg.type === 'event' && msg.event && msg.event.id != null) {
        appendEvent(msg.session_id, msg.event);
      }
      scheduleInvalidation(msg.session_id);
    };

    function onVisible() {
      if (!document.hidden && state.pendingSessions.size > 0 && !state.timer) {
        flushInvalidations();
      }
    }
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      es.close();
      document.removeEventListener('visibilitychange', onVisible);
      if (state.timer) clearTimeout(state.timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient]);
}
