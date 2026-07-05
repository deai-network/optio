import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Segmented, Select, Spin, Switch, Tooltip, theme } from 'antd';
import type { GlobalToken } from 'antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import type { ChatItem, ChatState, SessionControl } from './chat.js';
import { AnswerBlock } from './AnswerBlock.js';
import { type Attachment, toAttachment, withinCap } from './attachments.js';
import { FileDownloadContext } from './FileDownloadContext.js';

// Shared conversation chrome for every engine view. Each engine view reduces
// its native wire events into the engine-neutral ChatState, then hands the
// rendering, local UI state, the input bar, and a thin header to this single
// component — only the transport callbacks and the declared session controls
// (model / thinking / mode / ...) differ between engines.

export interface ConversationViewProps {
  state: ChatState;
  closed: boolean;
  busy: boolean;
  toolVerbosity: 'silent' | 'description-only' | 'verbose';
  // Reasoning/thinking traces (e.g. grok's agent_thought_chunk). 'hidden' → not
  // rendered; 'visible' → shown in a distinct reasoning style. Task-level, set
  // by the engine via widgetData — the view never decides visibility itself.
  thinkingVerbosity: 'hidden' | 'visible';
  showFileUpload: boolean;
  maxUploadBytes: number;
  fileDownload: boolean;
  onSend: (text: string, attachments: Attachment[]) => Promise<boolean>; // returns ok
  onInterrupt: () => void;
  onPermission: (requestId: string, behavior: 'allow' | 'deny') => void;
  onFileDownload: (relpath: string, filename: string) => void;
  // Engine-neutral session controls (model / thinking / mode / ...) rendered
  // generically in the input bar; onControlChange channels a value change back
  // to the wrapper (POST /control or UI-local).
  controls?: SessionControl[];
  onControlChange?: (id: string, value: string | boolean) => void;
  // theming (only set by ConversationWidget when ownTheme):
  themeMode?: 'light' | 'dark';
  onToggleTheme?: () => void; // absent => no ☀/🌙 button
}

const bubbleBase: React.CSSProperties = {
  maxWidth: '80%',
  padding: '6px 10px',
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
};

// One-time mount flash: a thick pulsating ring (box-shadow, so it doesn't
// shift layout) that plays ~4×0.5s = 2s then stops. Injected once into the
// document head — the package otherwise uses inline styles, but @keyframes
// can't be expressed inline. The same style id keeps it installed once even if
// multiple views mount.
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

// Hover-revealed per-answer copy control. Scoped to the .optio-cc-answer
// wrapper so it never leaks to global selectors.
const COPY_STYLE_ID = 'optio-cc-copy-style';
function ensureCopyStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(COPY_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = COPY_STYLE_ID;
  el.textContent = `.optio-cc-answer .optio-cc-copy{visibility:hidden}
  .optio-cc-answer:hover .optio-cc-copy{visibility:visible}`;
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

// Generic renderer for engine-neutral session controls. Each control renders by
// kind: boolean -> <Switch>, segmented -> <Segmented>, select -> <Select>
// (disabled options greyed with a whyDisabled tooltip title). Every control
// carries a `control-<id>` data-testid.
function SessionControls({
  controls, disabled, onChange,
}: {
  controls: SessionControl[];
  disabled: boolean;
  onChange: (id: string, value: string | boolean) => void;
}) {
  if (!controls.length) return null;
  return (
    <>
      {controls.map((c) => {
        if (c.kind === 'boolean') {
          return (
            <Switch
              key={c.id}
              data-testid={`control-${c.id}`}
              size="small"
              checked={Boolean(c.value)}
              disabled={disabled}
              onChange={(v) => onChange(c.id, v)}
            />
          );
        }
        if (c.kind === 'segmented') {
          return (
            <Segmented
              key={c.id}
              data-testid={`control-${c.id}`}
              size="small"
              value={String(c.value)}
              disabled={disabled}
              options={(c.levels ?? []).map((l) => ({ label: l, value: l }))}
              onChange={(v) => onChange(c.id, String(v))}
            />
          );
        }
        // select
        return (
          <Select
            key={c.id}
            data-testid={`control-${c.id}`}
            size="small"
            style={{ minWidth: 180, alignSelf: 'center' }}
            placeholder={c.label}
            disabled={disabled}
            value={c.value ? String(c.value) : undefined}
            onChange={(v: string) => onChange(c.id, v)}
            options={(c.options ?? []).map((o) => ({
              label: o.label,
              value: o.value,
              disabled: o.disabled,
              title: o.whyDisabled,
            }))}
          />
        );
      })}
    </>
  );
}

export function ConversationView(props: ConversationViewProps): React.JSX.Element {
  const { token } = theme.useToken();
  const {
    state,
    closed,
    busy,
    toolVerbosity,
    thinkingVerbosity,
    showFileUpload,
    maxUploadBytes,
    fileDownload,
    onSend,
    onInterrupt,
    onPermission,
    onFileDownload,
  } = props;

  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [wide, setWide] = useState(false);
  const inputRef = useRef<TextAreaRef>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const programmaticRef = useRef(false);
  const lastContentHeightRef = useRef(0);

  // On mount: install the flash keyframes + copy hover rule and focus the input
  // so the operator can type immediately without clicking. The widget mounts
  // async (it un-gates only once widgetData arrives), so on a full page-load a
  // single focus() can land before the page settles and not stick — re-assert
  // it on short delays.
  useEffect(() => {
    ensureFlashStyle();
    ensureCopyStyle();
    inputRef.current?.focus();
    const timers = [100, 400, 1000].map((ms) => setTimeout(() => inputRef.current?.focus(), ms));
    return () => timers.forEach(clearTimeout);
  }, []);

  // Global Escape-to-interrupt: a window-level handler so the operator can stop
  // a running turn from anywhere in the widget, not only from the input box.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && props.busy && !props.closed) {
        e.preventDefault();
        props.onInterrupt();
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.busy, props.closed]);

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

  async function send() {
    const body = text;
    if (!body || sending || closed) return;
    setSending(true);
    setError(null);
    const ok = await onSend(body, attachments);
    if (ok) {
      setText('');
      setAttachments([]);
    } else {
      setError('Send failed — retry.');
    }
    setSending(false);
    // Keep the keyboard on the input so the operator can keep typing after
    // Enter without a mouse click.
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    } else if (e.key === 'Escape' && busy && !closed) {
      // Same guard as the Interrupt button: only while a turn is running.
      e.preventDefault();
      onInterrupt();
    }
  }

  function renderItem(item: ChatItem) {
    switch (item.kind) {
      case 'user':
        return (
          <div
            key={item.seq}
            style={{
              ...bubbleBase,
              alignSelf: 'flex-end',
              background: token.colorPrimaryBg,
              border: `1px solid ${token.colorPrimaryBorder}`,
              borderRadius: '14px 14px 4px 14px',
              // Explicit token color — without it the text inherits the host's
              // default and is unreadable on the dark-mode bubble.
              color: token.colorText,
            }}
          >
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
              borderRadius: '14px 14px 14px 4px',
            }}
          >
            <div className="optio-cc-answer" style={{ position: 'relative' }}>
              <AnswerBlock text={item.text} />
              <Button
                size="small"
                type="text"
                className="optio-cc-copy"
                data-testid="answer-copy"
                style={{ position: 'absolute', top: 0, right: 0 }}
                onClick={() => void navigator.clipboard?.writeText(item.text)}
              >
                ⧉
              </Button>
            </div>
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
              borderRadius: 14,
            }}
          >
            {item.text}
          </div>
        );
      case 'thinking': {
        // Reasoning trace — task-gated (thinkingVerbosity) and styled close to a
        // reply (left-aligned like the assistant), but secondary: dimmed, italic,
        // with a subtle left rule and a small "Reasoning" caption. Deliberately
        // NOT the centered lavender System-message style.
        if (thinkingVerbosity === 'hidden') return null;
        return (
          <div
            key={item.seq}
            data-testid="thinking"
            style={{
              alignSelf: 'flex-start',
              maxWidth: '80%',
              padding: '2px 10px',
              borderLeft: `2px solid ${token.colorBorder}`,
              color: token.colorTextTertiary,
              fontStyle: 'italic',
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
            }}
          >
            <div style={{ fontSize: 11, fontStyle: 'normal', opacity: 0.7, marginBottom: 2 }}>
              Reasoning
            </div>
            {item.text}
          </div>
        );
      }
      case 'tool': {
        if (toolVerbosity === 'silent') return null;
        const summary = toolVerbosity === 'description-only' ? toolSummary(item.input) : '';
        return (
          <div key={item.seq} data-testid="tool-call" style={{ color: token.colorTextTertiary, fontSize: 12 }}>
            <div style={{ fontFamily: 'monospace' }}>
              running <strong>{item.name}</strong>
              {summary ? `: ${summary}` : ':'}
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
                onClick={() => onPermission(item.requestId, 'allow')}
              >
                Approve
              </Button>
              <Button
                size="small"
                danger
                data-testid="permission-deny"
                onClick={() => onPermission(item.requestId, 'deny')}
              >
                Deny
              </Button>
            </div>
          </div>
        );
      case 'error':
        return (
          <div
            key={item.seq}
            data-testid="conversation-error-item"
            style={{
              alignSelf: 'stretch',
              background: token.colorErrorBg,
              border: `1px solid ${token.colorErrorBorder}`,
              color: token.colorErrorText,
              borderRadius: 8,
              padding: '8px 12px',
              whiteSpace: 'pre-wrap',
            }}
          >
            {item.text}
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
    <FileDownloadContext.Provider value={fileDownload ? onFileDownload : null}>
      {/* Paint our own root surface from a bg token — the widget owns its
          background (don't rely on an inherited host surface), or dark mode
          shows the light host page behind transparent divs. colorText sets a
          themed default for any inherited-color text inside. */}
      <div style={{
        display: 'flex', flexDirection: 'column', width: '100%', height: '100%',
        background: token.colorBgLayout, color: token.colorText,
      }}>
        <div
          data-testid="conversation-header"
          style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '4px 8px' }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: token.colorTextSecondary }}>
            Wide
            <Switch size="small" data-testid="wide-toggle" checked={wide} onChange={setWide} />
          </span>
          {props.onToggleTheme && (
            <Button size="small" data-testid="theme-toggle" onClick={props.onToggleTheme}>
              {props.themeMode === 'dark' ? '☀' : '🌙'}
            </Button>
          )}
        </div>
        <div
          ref={scrollRef}
          onScroll={onScroll}
          style={{
            flex: 1, minHeight: 0, overflowY: 'auto', padding: 8,
            // The scrollbar is browser-painted (not antd-tokened); drive it from
            // tokens so it follows light/dark. Standard property — modern
            // Chrome/Firefox/Safari support it; older engines fall back to default.
            scrollbarWidth: 'thin',
            scrollbarColor: `${token.colorTextQuaternary} transparent`,
          }}
        >
          {/* Inner wrapper is what the ResizeObserver watches — the scroll
              container's own box is fixed (flex:1), so only this content node
              reports the height growth that drives auto-scroll. The max-width
              reading column centers and caps the transcript unless `wide`. */}
          <div
            ref={contentRef}
            data-testid="conversation-content"
            style={{
              maxWidth: wide ? '100%' : 880,
              margin: '0 auto',
              width: '100%',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
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
              every frame, forcing a reflow-per-frame (100% CPU, unresponsive
              input) while the agent worked. */}
          {busy && !closed && (
            <div style={{
              // Align with the centered reading column (same cap + margin as the
              // content node) so the indicator lines up with the replies, not the
              // scroll-area edge. Stays a SIBLING of contentRef (see above).
              maxWidth: wide ? '100%' : 880, margin: '0 auto', width: '100%',
              display: 'flex', alignItems: 'center', gap: 8, color: token.colorTextTertiary,
            }}>
              <Spin size="small" /> working…
            </div>
          )}
        </div>
        {attachments.length > 0 && (
          <div data-testid="attach-chips" style={{ maxWidth: wide ? '100%' : 880, margin: '0 auto', width: '100%', display: 'flex', flexWrap: 'wrap', gap: 4, padding: '4px 8px' }}>
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
        <div style={{ borderTop: `1px solid ${token.colorBorderSecondary}`, padding: 8 }}>
          {/* Cap the composer to the reading column (honor the wide toggle) so
              it aligns with the messages; the bar/border stays full-width. */}
          <div style={{
            maxWidth: wide ? '100%' : 880, margin: '0 auto', width: '100%',
            display: 'flex', flexDirection: 'column', gap: 8,
          }}>
          {error && (
            <Alert
              type="error"
              closable
              message={error}
              onClose={() => setError(null)}
              data-testid="conversation-error"
              style={{ marginBottom: 4 }}
            />
          )}
          {/* Row 1: the message input (full width). */}
          <Input.TextArea
            data-testid="conversation-input-box"
            className="optio-cc-flash"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Message agent…  (Enter to send, Shift+Enter for newline)"
            autoSize={{ minRows: 2, maxRows: 8 }}
            disabled={closed}
            ref={inputRef}
          />
          {/* Row 2: a single-height toolbar — model + attach on the left,
              Send/Interrupt pushed right. All size="small" so heights match. */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {props.controls && props.onControlChange ? (
              <SessionControls
                controls={props.controls}
                disabled={props.busy || props.state.closed}
                onChange={props.onControlChange}
              />
            ) : null}
            <div style={{ flex: 1 }} />
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
                <Tooltip title="Attach files">
                  <Button
                    size="small"
                    data-testid="attach-button"
                    disabled={closed}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    📎
                  </Button>
                </Tooltip>
              </>
            )}
            <Button
              size="small"
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
              onClick={onInterrupt}
            >
              Interrupt
            </Button>
          </div>
          </div>
        </div>
      </div>
    </FileDownloadContext.Provider>
  );
}
