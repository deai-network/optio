import { useEffect, useReducer, useRef, useState } from 'react';
import { Button, Spin } from 'antd';
import type { WidgetProps } from 'optio-ui';
import { registerWidget } from 'optio-ui';
import type { ChatItem, ChatState } from './events.js';
import { initialChatState, reduceEvent } from './events.js';

interface ChatAction {
  ev: unknown;
  seq: number;
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  return reduceEvent(state, action.ev, action.seq);
}

const bubbleBase: React.CSSProperties = {
  maxWidth: '80%',
  padding: '6px 10px',
  borderRadius: 8,
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
};

export function ClaudeCodeConversationWidget(props: WidgetProps) {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [sendPending, setSendPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  const { widgetProxyUrl } = props; // ends with '/' — trailing slash is load-bearing

  useEffect(() => {
    const es = new EventSource(`${widgetProxyUrl}events`);
    es.onmessage = (ev: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(ev.data);
      } catch {
        return;
      }
      dispatch({ ev: parsed, seq: Number(ev.lastEventId) });
    };
    return () => es.close();
  }, [widgetProxyUrl]);

  // Optimistic busy bridges the gap between POST /send and the echoed user
  // event; the reducer's busy is authoritative once any echo arrives.
  useEffect(() => {
    if (state.busy || state.closed) setSendPending(false);
  }, [state.busy, state.closed]);
  const busy = state.busy || sendPending;

  // Auto-scroll to the bottom on append, unless the user scrolled up.
  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [state.items]);

  async function post(path: string, body: unknown): Promise<boolean> {
    try {
      const resp = await fetch(`${widgetProxyUrl}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      return resp.ok;
    } catch {
      return false;
    }
  }

  async function send() {
    const body = text;
    if (!body || sending || state.closed) return;
    setSending(true);
    setError(null);
    const ok = await post('send', { text: body });
    if (ok) {
      setText('');
      setSendPending(true);
    } else {
      setError('Send failed — retry.');
    }
    setSending(false);
    // Keep the keyboard on the input so the operator can keep typing after
    // Enter without a mouse click. The textarea is never disabled while the
    // conversation is open; this refocus also covers any other blur source.
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  function answerPermission(requestId: string, behavior: 'allow' | 'deny') {
    void post('permission', { request_id: requestId, behavior });
  }

  function renderItem(item: ChatItem) {
    switch (item.kind) {
      case 'user':
        return (
          <div key={item.seq} style={{ ...bubbleBase, alignSelf: 'flex-end', background: '#e6f4ff' }}>
            {item.text}
          </div>
        );
      case 'assistant':
        return (
          <div
            key={item.seq}
            style={{ ...bubbleBase, alignSelf: 'flex-start', background: '#fff', border: '1px solid #ddd' }}
          >
            {item.text}
            {item.pending && <span style={{ color: '#999' }}>▍</span>}
          </div>
        );
      case 'activity':
        return (
          <div key={item.seq} style={{ color: '#888', fontFamily: 'monospace', fontSize: 12 }}>
            {item.text}
          </div>
        );
      case 'permission':
        return (
          <div
            key={item.seq}
            data-testid="permission-card"
            style={{
              alignSelf: 'stretch',
              border: '1px solid #faad14',
              background: '#fffbe6',
              borderRadius: 8,
              padding: 8,
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
            }}
          >
            <div>
              Permission requested: <strong>{item.toolName}</strong>
            </div>
            <div style={{ fontFamily: 'monospace', fontSize: 12, color: '#666', overflowWrap: 'anywhere' }}>
              {JSON.stringify(item.input)?.slice(0, 200)}
            </div>
            {item.answered === null ? (
              <div style={{ display: 'flex', gap: 8 }}>
                <Button
                  size="small"
                  type="primary"
                  data-testid="permission-approve"
                  onClick={() => answerPermission(item.requestId, 'allow')}
                >
                  Approve
                </Button>
                <Button
                  size="small"
                  danger
                  data-testid="permission-deny"
                  onClick={() => answerPermission(item.requestId, 'deny')}
                >
                  Deny
                </Button>
              </div>
            ) : (
              <div style={{ color: '#888' }}>{item.answered === 'allow' ? 'Allowed' : 'Denied'}</div>
            )}
          </div>
        );
      case 'closed':
        return (
          <div
            key={item.seq}
            style={{ alignSelf: 'stretch', display: 'flex', alignItems: 'center', gap: 8, color: '#888' }}
          >
            <div style={{ flex: 1, borderTop: '1px solid #ddd' }} />
            <span>conversation ended{item.reason ? ` (${item.reason})` : ''}</span>
            <div style={{ flex: 1, borderTop: '1px solid #ddd' }} />
          </div>
        );
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          padding: 8,
        }}
      >
        {state.items.map(renderItem)}
        {busy && !state.closed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#888' }}>
            <Spin size="small" /> working…
          </div>
        )}
      </div>
      <div style={{ borderTop: '1px solid #ddd', padding: 8, display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea
          ref={inputRef}
          data-testid="conversation-input-box"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Message Claude…  (Enter to send, Shift+Enter for newline)"
          rows={2}
          disabled={state.closed}
          style={{ flex: 1, resize: 'vertical', fontFamily: 'inherit' }}
        />
        <button
          data-testid="conversation-send"
          onClick={() => void send()}
          disabled={sending || !text || state.closed}
        >
          Send
        </button>
        <Button
          size="small"
          danger
          data-testid="conversation-interrupt"
          disabled={!busy || state.closed}
          onClick={() => void post('interrupt', {})}
        >
          Interrupt
        </Button>
        {error && (
          <span data-testid="conversation-error" style={{ color: '#b00', alignSelf: 'center' }}>
            {error}
          </span>
        )}
      </div>
    </div>
  );
}

export function registerClaudeCodeConversationWidget(): void {
  registerWidget('claudecode-conversation', ClaudeCodeConversationWidget);
}
