import { useEffect, useReducer, useRef, useState } from 'react';
import { Button, Spin, theme } from 'antd';
import type { GlobalToken } from 'antd';
import { isTerminalState } from 'optio-ui';
import type { WidgetProps } from 'optio-ui';
import type { ChatItem, ChatState } from '../chat.js';
import { initialChatState } from '../chat.js';
import { historyToChatItems, reduceOpencodeEvent } from './events.js';
import { AnswerBlock } from '../AnswerBlock.js';

// Conversation view for opencode tasks: speaks opencode's native HTTP+SSE API
// through the widget proxy (exactly like iframe mode does), reduces the wire
// events into the shared ChatState, and renders the same conversation chrome
// as ClaudeCodeView. Only the transport differs between the two views.

interface OpencodeWidgetData {
  sessionID?: string;
  directory?: string;
  toolVerbosity?: 'silent' | 'description-only' | 'verbose';
}

type ChatAction = { kind: 'bootstrap'; items: ChatItem[] } | { ev: unknown; seq: number };

const bubbleBase: React.CSSProperties = {
  maxWidth: '80%',
  padding: '6px 10px',
  borderRadius: 8,
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
};

// One-time mount flash (same keyframes as ClaudeCodeView — the shared style id
// makes whichever view mounts first install it once for both).
const FLASH_STYLE_ID = 'optio-cc-flash-style';
function ensureFlashStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(FLASH_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = FLASH_STYLE_ID;
  el.textContent = `@keyframes optio-cc-flash {
    0%   { box-shadow: 0 0 0 0 rgba(24,144,255,0.0); }
    50%  { box-shadow: 0 0 0 6px rgba(24,144,255,0.6); }
    100% { box-shadow: 0 0 0 0 rgba(24,144,255,0.0); }
  }
  .optio-cc-flash { animation: optio-cc-flash 0.5s ease-in-out 0s 4; }`;
  document.head.appendChild(el);
}

// Colors come from the antd theme (ConfigProvider algorithm), so the widget
// follows the host app's light/dark switch instead of a hardcoded palette.
function kvCell(token: GlobalToken): React.CSSProperties {
  return {
    border: `1px solid ${token.colorWarningBorder}`,
    padding: '2px 6px',
    verticalAlign: 'top',
    fontFamily: 'monospace',
    fontSize: 12,
  };
}

// Render a tool-permission input object as a key→value table. Falls back to a
// JSON string for non-object inputs (a bare string/array argument).
function renderInputKV(input: unknown, token: GlobalToken): React.ReactNode {
  const cell = kvCell(token);
  if (input && typeof input === 'object' && !Array.isArray(input)) {
    const entries = Object.entries(input as Record<string, unknown>);
    if (entries.length === 0) return null;
    return (
      <table style={{ borderCollapse: 'collapse', width: 'auto', maxWidth: '100%' }}>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k}>
              <td style={{ ...cell, fontWeight: 600, whiteSpace: 'nowrap', color: token.colorWarningText }}>{k}</td>
              <td style={{ ...cell, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: token.colorTextSecondary }}>
                {typeof v === 'string' ? v : JSON.stringify(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return (
    <div style={{ fontFamily: 'monospace', fontSize: 12, color: token.colorTextSecondary, overflowWrap: 'anywhere' }}>
      {JSON.stringify(input)}
    </div>
  );
}

// For description-only verbosity: pick a one-line summary from the tool input —
// its `description` when present, else the first non-empty string under a
// salient key, truncated. Empty string => show just the tool name.
const SALIENT_KEYS = ['description', 'command', 'file_path', 'path', 'pattern', 'query', 'url', 'prompt', 'title'];
function toolSummary(input: unknown): string {
  if (input && typeof input === 'object' && !Array.isArray(input)) {
    const obj = input as Record<string, unknown>;
    for (const k of SALIENT_KEYS) {
      const v = obj[k];
      if (typeof v === 'string' && v.trim()) {
        const s = v.trim();
        return s.length > 120 ? s.slice(0, 117) + '…' : s;
      }
    }
  }
  return '';
}

export function OpencodeView(props: WidgetProps) {
  // Gate on widgetData like IframeWidget does — the session id and directory
  // are the transport's addressing and arrive with the widget data.
  const widgetData = (props.process?.widgetData ?? undefined) as OpencodeWidgetData | undefined;
  if (!widgetData?.sessionID) {
    return <div data-testid="optio-widget-loading">Loading…</div>;
  }
  return <OpencodeChat {...props} sessionID={widgetData.sessionID} directory={widgetData.directory ?? ''} />;
}

function OpencodeChat(props: WidgetProps & { sessionID: string; directory: string }) {
  const { token } = theme.useToken();
  const { sessionID, directory, widgetProxyUrl } = props; // widgetProxyUrl ends with '/' — trailing slash is load-bearing
  const toolVerbosity = ((props.process.widgetData as any)?.toolVerbosity ?? 'description-only') as
    'silent' | 'description-only' | 'verbose';
  // opencode routes resolve their project instance from the request's
  // location context — every session-scoped call carries ?directory=.
  const q = `?directory=${encodeURIComponent(directory)}`;

  const [state, dispatch] = useReducer(
    (s: ChatState, action: ChatAction): ChatState => {
      if ('kind' in action && action.kind === 'bootstrap') {
        return { ...s, items: action.items };
      }
      const { ev, seq } = action as { ev: unknown; seq: number };
      return reduceOpencodeEvent(s, ev, seq, sessionID);
    },
    initialChatState,
  );
  const seqRef = useRef(0);
  const localSeqRef = useRef(-1);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const programmaticRef = useRef(false);
  const lastContentHeightRef = useRef(0);

  // "Session ended": the opencode server (and the proxy route to it) dies with
  // the task process, so closed derives from the process document's terminal
  // state — same predicate IframeWidget uses for its banner.
  const closed = state.closed || isTerminalState(props.process?.status?.state);

  // Bootstrap: subscribe FIRST and buffer, then fetch history and reconcile —
  // events that arrive between the two calls are replayed after the bootstrap
  // (the spec's history-then-subscribe race fix). Live events come from
  // /global/event: the per-instance /event?directory=… stream ends right
  // after server.connected (observed on 1.14.45) and is unusable.
  useEffect(() => {
    console.info('[optio-conversation-ui] opencode conversation view activated:', `${widgetProxyUrl}global/event`);
    let bootstrapped = false;
    const buffer: any[] = [];
    const es = new EventSource(`${widgetProxyUrl}global/event`);
    es.onmessage = (msg) => {
      let ev: any;
      try { ev = JSON.parse(msg.data); } catch { return; }
      if (!bootstrapped) { buffer.push(ev); return; }
      dispatch({ ev, seq: seqRef.current++ });
    };
    void (async () => {
      try {
        const resp = await fetch(`${widgetProxyUrl}session/${sessionID}/message${q}`);
        const history = resp.ok ? await resp.json() : [];
        dispatch({ kind: 'bootstrap', items: historyToChatItems(history, sessionID) });
      } finally {
        bootstrapped = true;
        for (const ev of buffer) dispatch({ ev, seq: seqRef.current++ });
      }
    })();
    return () => es.close();
  }, [widgetProxyUrl, sessionID]);

  // On mount: install the flash keyframes and focus the input so the operator
  // can type immediately without clicking. The widget mounts async (it un-gates
  // only once widgetData arrives), so on a full page-load a single focus() can
  // land before the page settles and not stick — re-assert it on short delays.
  useEffect(() => {
    ensureFlashStyle();
    inputRef.current?.focus();
    const timers = [100, 400, 1000].map((ms) =>
      setTimeout(() => inputRef.current?.focus(), ms),
    );
    return () => timers.forEach(clearTimeout);
  }, []);

  // busy is purely reducer-driven: the optimistic local-user echo sets it on
  // send, session.status busy/idle tracks the turn from the wire.
  const busy = state.busy;

  // Auto-grow the input with its content (up to the maxHeight cap, after which
  // it scrolls): reset to auto to shrink on deletion, then fit to scrollHeight.
  useEffect(() => {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${ta.scrollHeight}px`;
  }, [text]);

  // Auto-scroll to the bottom while streaming, unless the operator scrolled up.
  // (Same ResizeObserver re-pin mechanism as ClaudeCodeView — see the
  // commentary there for why a plain items-effect is not enough.)
  function pinToBottom() {
    const el = scrollRef.current;
    if (!el) return;
    programmaticRef.current = true;
    el.scrollTop = el.scrollHeight;
    requestAnimationFrame(() => {
      programmaticRef.current = false;
    });
  }
  function onScroll() {
    if (programmaticRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const ro = new ResizeObserver((entries) => {
      // Re-pin ONLY when the content actually grew (no reflow-per-frame loop).
      const h = entries[0]?.contentRect.height ?? 0;
      if (h <= lastContentHeightRef.current) {
        lastContentHeightRef.current = h;
        return;
      }
      lastContentHeightRef.current = h;
      if (stickToBottomRef.current) pinToBottom();
    });
    ro.observe(content);
    return () => ro.disconnect();
  }, []);

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
    if (!body || sending || closed) return;
    setSending(true);
    setError(null);
    const ok = await post(`session/${sessionID}/prompt_async${q}`, { parts: [{ type: 'text', text: body }] });
    if (ok) {
      // Optimistic local echo: show the message now; the wire echo
      // (message.updated role=user) confirms it in place. Negative seqs keep
      // React keys unique and clear of wire seqs.
      dispatch({ ev: { type: 'x-optio-local-user', properties: { text: body } }, seq: localSeqRef.current-- });
      setText('');
    } else {
      setError('Send failed — retry.');
    }
    setSending(false);
    // Keep the keyboard on the input so the operator can keep typing after
    // Enter without a mouse click.
    inputRef.current?.focus();
  }

  function interrupt() {
    void post(`session/${sessionID}/abort${q}`, {});
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    } else if (e.key === 'Escape' && busy && !closed) {
      // Same guard as the Interrupt button: only while a turn is running.
      e.preventDefault();
      interrupt();
    }
  }

  function answerPermission(requestId: string, behavior: 'allow' | 'deny') {
    // Spec mapping: allow → reply "once"; deny → reply "reject" with a
    // human-readable message. The permission.replied wire event flips the
    // card's answered state.
    const body =
      behavior === 'deny'
        ? { reply: 'reject', message: 'Denied by the operator.' }
        : { reply: 'once' };
    void post(`permission/${requestId}/reply${q}`, body);
  }

  function renderItem(item: ChatItem) {
    switch (item.kind) {
      case 'user':
        return (
          <div key={item.seq} style={{ ...bubbleBase, alignSelf: 'flex-end', background: token.colorPrimaryBg }}>
            {item.text}
          </div>
        );
      case 'assistant':
        return (
          <div
            key={item.seq}
            style={{
              ...bubbleBase,
              alignSelf: 'flex-start',
              background: token.colorBgContainer,
              border: `1px solid ${token.colorBorderSecondary}`,
            }}
          >
            <AnswerBlock text={item.text} />
            {item.pending && <span style={{ color: token.colorTextTertiary }}>▍</span>}
          </div>
        );
      case 'activity':
        return (
          <div
            key={item.seq}
            style={{
              ...bubbleBase,
              alignSelf: 'center',
              background: token.purple1,
              border: `1px solid ${token.purple3}`,
              color: token.colorTextSecondary,
              fontSize: 12,
            }}
          >
            {item.text}
          </div>
        );
      case 'tool': {
        if (toolVerbosity === 'silent') return null;
        const summary = toolVerbosity === 'description-only' ? toolSummary(item.input) : '';
        return (
          <div key={item.seq} data-testid="tool-call" style={{ color: token.colorTextTertiary, fontSize: 12 }}>
            <div style={{ fontFamily: 'monospace' }}>
              running <strong>{item.name}</strong>{summary ? `: ${summary}` : ':'}
            </div>
            {toolVerbosity === 'verbose' ? renderInputKV(item.input, token) : null}
          </div>
        );
      }
      case 'permission':
        // Once answered, hide the dialog entirely — the conversation proceeds.
        if (item.answered !== null) return null;
        return (
          <div
            key={item.seq}
            data-testid="permission-card"
            style={{
              alignSelf: 'stretch',
              border: `1px solid ${token.colorWarning}`,
              background: token.colorWarningBg,
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
            {renderInputKV(item.input, token)}
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
          </div>
        );
      case 'closed':
        return (
          <div
            key={item.seq}
            style={{
              alignSelf: 'stretch',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              color: token.colorTextTertiary,
            }}
          >
            <div style={{ flex: 1, borderTop: `1px solid ${token.colorBorderSecondary}` }} />
            <span>conversation ended{item.reason ? ` (${item.reason})` : ''}</span>
            <div style={{ flex: 1, borderTop: `1px solid ${token.colorBorderSecondary}` }} />
          </div>
        );
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 8 }}
      >
        {/* Inner wrapper is what the ResizeObserver watches — the scroll
            container's own box is fixed (flex:1), so only this content node
            reports the height growth that drives auto-scroll. */}
        <div ref={contentRef} data-testid="conversation-content" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {/* Items are kept in conversation order by the reducer; render in
              array order (seq is a React key, not a sort key). */}
          {state.items.map(renderItem)}
          {/* The process died (or the conversation was closed engine-side):
              append the ended divider after the transcript. */}
          {closed && (
            <div
              data-testid="conversation-closed"
              style={{
                alignSelf: 'stretch',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                color: token.colorTextTertiary,
              }}
            >
              <div style={{ flex: 1, borderTop: `1px solid ${token.colorBorderSecondary}` }} />
              <span>conversation ended</span>
              <div style={{ flex: 1, borderTop: `1px solid ${token.colorBorderSecondary}` }} />
            </div>
          )}
        </div>
        {/* The working indicator stays a SIBLING of the observed content node —
            an animated <Spin> inside contentRef re-triggers the ResizeObserver
            every frame (see ClaudeCodeView). */}
        {busy && !closed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: token.colorTextTertiary }}>
            <Spin size="small" /> working…
          </div>
        )}
      </div>
      <div
        style={{
          borderTop: `1px solid ${token.colorBorderSecondary}`,
          padding: 8,
          display: 'flex',
          gap: 8,
          alignItems: 'flex-end',
        }}
      >
        <textarea
          ref={inputRef}
          className="optio-cc-flash"
          data-testid="conversation-input-box"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Message agent…  (Enter to send, Shift+Enter for newline)"
          rows={2}
          disabled={closed}
          style={{
            flex: 1,
            resize: 'none',
            fontFamily: 'inherit',
            maxHeight: 200,
            overflowY: 'auto',
            borderRadius: 6,
            padding: '6px 8px',
            background: token.colorBgContainer,
            color: token.colorText,
            border: `1px solid ${token.colorBorder}`,
          }}
        />
        <Button
          data-testid="conversation-send"
          type="primary"
          onClick={() => void send()}
          disabled={sending || !text || closed}
        >
          Send
        </Button>
        <Button
          size="small"
          danger
          data-testid="conversation-interrupt"
          disabled={!busy || closed}
          onClick={interrupt}
        >
          Interrupt
        </Button>
        {error && (
          <span data-testid="conversation-error" style={{ color: token.colorError, alignSelf: 'center' }}>
            {error}
          </span>
        )}
      </div>
    </div>
  );
}
