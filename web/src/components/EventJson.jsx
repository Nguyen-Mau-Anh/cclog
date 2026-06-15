import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchEventJson } from '../api';

// Tokenize a JSON string into typed segments for React rendering.
// Returns JSX-safe values — React escapes string nodes by design, no
// dangerouslySetInnerHTML needed.
function tokenize(json) {
  const tokens = [];
  const re = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  let last = 0;
  let m;

  while ((m = re.exec(json)) !== null) {
    if (m.index > last) tokens.push({ type: 'plain', value: json.slice(last, m.index) });

    const [full, str, colon, keyword, num] = m;
    if (str !== undefined && colon) {
      tokens.push({ type: 'key', value: str });
      tokens.push({ type: 'plain', value: colon });
    } else if (str !== undefined) {
      // Unescape \n/\t so shell output / file content shows with real line
      // breaks instead of literal \n sequences.
      const display = str.replace(/\\n/g, '\n').replace(/\\t/g, '\t').replace(/\\r/g, '');
      tokens.push({ type: 'str', value: display });
    } else if (keyword !== undefined) {
      tokens.push({ type: keyword === 'null' ? 'null' : 'bool', value: keyword });
    } else if (num !== undefined) {
      tokens.push({ type: 'num', value: num });
    }
    last = m.index + full.length;
  }
  if (last < json.length) tokens.push({ type: 'plain', value: json.slice(last) });
  return tokens;
}

function CopyButton({ value }) {
  const [state, setState] = useState('idle'); // 'idle' | 'copied' | 'error'
  function handleCopy() {
    navigator.clipboard.writeText(JSON.stringify(value, null, 2)).then(
      () => { setState('copied'); setTimeout(() => setState('idle'), 2000); },
      () => { setState('error'); setTimeout(() => setState('idle'), 2000); },
    );
  }
  return (
    <button
      type="button"
      className={`ev-json-copy ev-json-copy--${state}`}
      title="Copy to clipboard"
      onClick={handleCopy}
    >
      {state === 'copied' ? '✓' : state === 'error' ? '✗' : '⎘'}
    </button>
  );
}

function JsonBlock({ label, value }) {
  if (value == null) return null;
  const tokens = useMemo(() => tokenize(JSON.stringify(value, null, 2)), [value]);
  return (
    <div className="ev-json-block">
      <div className="ev-json-label-row">
        <span className="ev-json-label">{label}</span>
        <CopyButton value={value} />
      </div>
      <pre className="ev-json-pre">
        {tokens.map((tok, i) =>
          tok.type === 'plain'
            ? tok.value
            : <span key={i} className={`json-${tok.type}`}>{tok.value}</span>
        )}
      </pre>
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
