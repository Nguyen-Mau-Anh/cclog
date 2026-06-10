import { useQuery } from '@tanstack/react-query';
import { fetchEventJson } from '../api';

function JsonBlock({ label, value }) {
  if (value == null) return null;
  return (
    <div className="ev-json-block">
      <div className="ev-json-label">{label}</div>
      <pre className="ev-json-pre">{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

/** Lazy request/response payload for one event. Cached forever once loaded
 * (event JSON is immutable); bounded by React Query's cache GC. */
export function EventJson({ eventId }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['eventJson', eventId],
    queryFn: () => fetchEventJson(eventId),
    staleTime: Infinity,
    gcTime: 10 * 60 * 1000,
  });

  if (isLoading) return <div className="ev-json-msg">Loading…</div>;
  if (isError) return <div className="ev-json-msg error">{String(error)}</div>;
  if (data?.input_json == null && data?.output_json == null) {
    return <div className="ev-json-msg">No data.</div>;
  }
  return (
    <div className="ev-json-panel">
      <JsonBlock label="Request" value={data.input_json} />
      <JsonBlock label="Response" value={data.output_json} />
    </div>
  );
}
