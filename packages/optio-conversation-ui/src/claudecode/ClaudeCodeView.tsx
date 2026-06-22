import { useEffect, useReducer, useRef, useState } from 'react';
import { Button, Select, Spin, theme } from 'antd';
import type { GlobalToken } from 'antd';
import type { WidgetProps } from 'optio-ui';
import type { ChatItem, ChatState } from '../chat.js';
import { initialChatState, reduceEvent } from './events.js';
import { AnswerBlock } from '../AnswerBlock.js';
import { type Attachment, toAttachment, withinCap } from '../attachments.js';

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

// One-time mount flash: a thick pulsating ring (box-shadow, so it doesn't
// shift layout) that plays ~4×0.5s = 2s then stops. Injected once into the
// document head — the package otherwise uses inline styles, but @keyframes
// can't be expressed inline.
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

export function ClaudeCodeView(props: WidgetProps) {
  const { token } = theme.useToken();
  const toolVerbosity = ((props.process.widgetData as any)?.toolVerbosity ?? 'description-only') as
    'silent' | 'description-only' | 'verbose';
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const localSeqRef = useRef(0);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentModel, setCurrentModel] = useState<string | undefined>(
    (props.process.widgetData as any)?.currentModel ?? undefined,
  );
  const showModelSelector = Boolean((props.process.widgetData as any)?.showModelSelector);
  const models: { id: string; label: string }[] = (props.process.widgetData as any)?.models ?? [];
  const showFileUpload = Boolean((props.process.widgetData as any)?.showFileUpload);
  const maxUploadBytes = Number((props.process.widgetData as any)?.maxUploadBytes ?? 10_000_000);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const programmaticRef = useRef(false);
  const lastContentHeightRef = useRef(0);

  const { widgetProxyUrl } = props; // ends with '/' — trailing slash is load-bearing

  useEffect(() => {
    console.info('[optio-claudecode-ui] conversation widget activated:', `${widgetProxyUrl}events`);
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

  // The optimistic local-user echo (dispatched on a successful send) sets
  // state.busy immediately, so busy is purely reducer-driven — no separate
  // send flag that a busy-change effect could fail to clear on a mid-turn send.
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
  //
  // A plain effect on state.items is not enough: partial-text deltas and
  // markdown reflow grow the content height across frames the effect never
  // re-runs for, so the view falls behind a streaming answer. Instead, a
  // ResizeObserver on the content wrapper re-pins on every height change.
  // `programmaticRef` suppresses the scroll event our own pin emits, so it
  // can't be misread as the operator scrolling away mid-growth.
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
      // Re-pin ONLY when the content actually grew. Reading height from the RO
      // entry avoids a forced reflow, and skipping no-growth fires stops the
      // callback from re-triggering itself — the reflow-per-frame CPU loop.
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

  // Claude Code uploads attachments into the session workdir via the listener's
  // multipart POST /upload (not the JSON `post` helper). Returns the stored
  // relpaths, or null on any failure so send() can surface a retry.
  async function uploadFiles(atts: Attachment[]): Promise<string[] | null> {
    const fd = new FormData();
    for (const a of atts) fd.append('file', a.file, a.filename);
    try {
      const resp = await fetch(`${widgetProxyUrl}upload`, { method: 'POST', body: fd });
      if (!resp.ok) return null;
      const j = await resp.json();
      return (j.files ?? []).map((f: any) => String(f.path));
    } catch {
      return null;
    }
  }

  async function send() {
    const body = text;
    if (!body || sending || state.closed) return;
    setSending(true);
    setError(null);
    // When files are attached, upload them first, then bundle one `System:`
    // notice line per stored file into the prompt so the agent can Read them
    // from the workdir. The optimistic echo still shows the operator's text
    // (`body`), not the System: preamble.
    let prompt = body;
    if (attachments.length > 0) {
      const paths = await uploadFiles(attachments);
      if (!paths) {
        setError('Upload failed — retry.');
        setSending(false);
        return;
      }
      const notice = paths.map((p) => `System: upload received, stored in ${p}`).join('\n');
      prompt = `${notice}\n\n${body}`;
    }
    const ok = await post('send', { text: prompt });
    if (ok) {
      // Optimistic local echo: show the message now; the wire echo (which
      // only arrives once the answer starts streaming) confirms it in place.
      // Negative seqs keep React keys unique and clear of wire seqs.
      localSeqRef.current -= 1;
      dispatch({ ev: { type: 'x-optio-local-user', text: body }, seq: localSeqRef.current });
      setText('');
      setAttachments([]);
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
    } else if (e.key === 'Escape' && busy && !state.closed) {
      // Same guard as the Interrupt button: only while a turn is running.
      e.preventDefault();
      void post('interrupt', {});
    }
  }

  function answerPermission(requestId: string, behavior: 'allow' | 'deny') {
    // Claude Code's can_use_tool schema wants a human-readable reason on deny;
    // send a default so a bare click satisfies it. (The wire also carries a
    // free-form message if a reason field is added later.)
    const body =
      behavior === 'deny'
        ? { request_id: requestId, behavior, message: 'Denied by the operator.' }
        : { request_id: requestId, behavior };
    void post('permission', body);
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
        // Harness System: messages — neither the user nor the agent, so render
        // a centered bubble in a distinct (lavender) colour, set apart from the
        // right-aligned user and left-aligned assistant bubbles.
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
          {/* Items are kept in conversation order by the reducer (it slots an
              echoed user message in front of the assistant bubble it
              triggered), so render in array order. seq is NOT a valid sort key:
              with --replay-user-messages Claude streams the answer before
              echoing the question, so the answer carries the earlier seq. */}
          {state.items.map(renderItem)}
        </div>
        {/* The working indicator is a SIBLING of the observed content node, not
            a child: its animated <Spin> inside `contentRef` re-triggered the
            ResizeObserver every frame, forcing a reflow-per-frame (100% CPU,
            unresponsive input) while the agent worked. */}
        {busy && !state.closed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: token.colorTextTertiary }}>
            <Spin size="small" /> working…
          </div>
        )}
      </div>
      {attachments.length > 0 && (
        <div data-testid="attach-chips" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '4px 8px' }}>
          {attachments.map((a, i) => (
            <span
              key={i}
              style={{
                fontSize: 12,
                padding: '2px 6px',
                border: `1px solid ${token.colorBorderSecondary}`,
                borderRadius: 4,
              }}
            >
              {a.filename}
              <a style={{ marginLeft: 6 }} onClick={() => setAttachments(attachments.filter((_, j) => j !== i))}>
                ×
              </a>
            </span>
          ))}
        </div>
      )}
      <div
        style={{
          borderTop: `1px solid ${token.colorBorderSecondary}`,
          padding: 8,
          display: 'flex',
          gap: 8,
          alignItems: 'flex-end',
        }}
      >
        {showFileUpload && (
          <>
            <input
              data-testid="file-input"
              type="file"
              multiple
              style={{ display: 'none' }}
              ref={fileInputRef}
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? []).map(toAttachment);
                const next = [...attachments, ...picked];
                if (!withinCap(next, maxUploadBytes)) {
                  setError('File too large.');
                  return;
                }
                setAttachments(next);
                e.target.value = '';
              }}
            />
            <Button
              size="small"
              data-testid="attach-button"
              disabled={state.closed}
              onClick={() => fileInputRef.current?.click()}
            >
              📎
            </Button>
          </>
        )}
        {showModelSelector && (
          <Select
            data-testid="model-select"
            size="small"
            style={{ minWidth: 180, alignSelf: 'center' }}
            placeholder="Model"
            disabled={busy || state.closed}
            value={currentModel}
            onChange={(v: string) => {
              setCurrentModel(v);                       // optimistic
              void post('model', { model: v });         // engine relaunches
            }}
            options={models.map((m) => ({ label: m.label, value: m.id }))}
          />
        )}
        <textarea
          ref={inputRef}
          className="optio-cc-flash"
          data-testid="conversation-input-box"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Message agent…  (Enter to send, Shift+Enter for newline)"
          rows={2}
          disabled={state.closed}
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
          disabled={sending || !text || state.closed}
        >
          Send
        </Button>
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
          <span data-testid="conversation-error" style={{ color: token.colorError, alignSelf: 'center' }}>
            {error}
          </span>
        )}
      </div>
    </div>
  );
}
