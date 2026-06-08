import { useRef, useState } from 'react';
import type { WidgetProps } from './registry.js';
import { registerWidget } from './registry.js';
import { IframeWidget } from './IframeWidget.js';

export function IframeInputWidget(props: WidgetProps) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const controlUrl =
    `${props.apiBaseUrl}/api/widget-control/${encodeURIComponent(props.database ?? '')}` +
    `/${encodeURIComponent(props.prefix)}/${props.process._id}`;

  async function submit() {
    const body = text;
    if (!body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch(controlUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text: body }),
      });
      if (resp.ok) {
        setText('');
      } else {
        setError(resp.status === 404 ? 'Session not running.' : 'Send failed — retry.');
      }
    } catch {
      setError('Send failed — retry.');
    } finally {
      setBusy(false);
      // Keep the keyboard on the input so the operator can keep typing after
      // Enter without a mouse click. The textarea is never disabled (disabling
      // is what blurred it); this refocus also covers any other blur source.
      inputRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        <IframeWidget {...props} />
      </div>
      <div style={{ borderTop: '1px solid #ddd', padding: 8, display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea
          ref={inputRef}
          data-testid="agent-input-box"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Message Claude…  (Enter to send, Shift+Enter for newline)"
          rows={2}
          style={{ flex: 1, resize: 'vertical', fontFamily: 'inherit' }}
        />
        <button data-testid="agent-input-send" onClick={() => void submit()} disabled={busy || !text}>
          Send
        </button>
        {error && (
          <span data-testid="agent-input-error" style={{ color: '#b00', alignSelf: 'center' }}>
            {error}
          </span>
        )}
      </div>
    </div>
  );
}

registerWidget('iframe-input', IframeInputWidget);
