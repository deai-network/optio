import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Segmented, Select, Slider, Spin, Switch, Tooltip, theme } from 'antd';
import type { GlobalToken } from 'antd';
import { CombinedActionButton, type ActionStatus } from 'vultus-antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import type { ChatItem, ChatState, SessionControl } from './chat.js';
import { AnswerBlock } from './AnswerBlock.js';
import { type Attachment, toAttachment, withinCap } from './attachments.js';
import { FileDownloadContext } from './FileDownloadContext.js';
import { formatDuration } from './duration.js';
import { formatMessageTime, formatMessageTimeFull } from './messageTime.js';

// Shared conversation chrome for every engine view. Each engine view reduces
// its native wire events into the engine-neutral ChatState, then hands the
// rendering, local UI state, the input bar, and a thin header to this single
// component — only the transport callbacks and the declared session controls
// (model / thinking / mode / ...) differ between engines.

export interface ConversationViewProps {
  state: ChatState;
  closed: boolean;
  busy: boolean;
  // 'silent' → no tool rows. 'description-while-active' → one line WHILE the
  // tool runs, hidden once finished (used by analysis tasks). At both, a
  // finished background job keeps one muted outcome line. 'description-only'
  // → a persistent one-line row (⟳/✓/✗/⏹). 'verbose' → the line plus the args/
  // result detail; a finished tool collapses to the line (click to expand).
  toolVerbosity: 'silent' | 'description-while-active' | 'description-only' | 'verbose';
  // Reasoning/thinking traces (e.g. grok's agent_thought_chunk). 'hidden' → not
  // rendered; 'visible' → shown in a distinct reasoning style. Task-level, set
  // by the engine via widgetData — the view never decides visibility itself.
  thinkingVerbosity: 'hidden' | 'visible';
  showFileUpload: boolean;
  maxUploadBytes: number;
  fileDownload: boolean;
  // On-brand "working" indicator, supplied by the engine view when the task
  // sets the native_spinner option (conversation mode only). When absent, the
  // generic antd <Spin> is used. The view passes its engine's NativeSpinner.
  nativeSpinner?: React.ReactNode;
  onSend: (text: string, attachments: Attachment[]) => Promise<boolean>; // returns ok
  // Resolves whether the request reached the listener (Fix 6, manual-test
  // finding 1): the Interrupt button shows a pending state while in flight
  // and 'Interrupt failed — retry.' through the same error slot the input
  // bar uses on false or a rejection. A void-returning handler (the other
  // engines, unchanged) is treated as immediate success — no pending
  // flicker, no error path.
  onInterrupt: () => Promise<boolean> | void;
  // Steering (optional). When set, a busy input bar offers "Send when ready"
  // (Enter → onSend) and "Interrupt and send" (Cmd/Ctrl-Enter → onSteer) in
  // one vultus multi-action button, and a queued bubble offers "Send now"
  // (onSteer('', [])). Absent: the bar keeps a single Send while busy (the
  // engine's /send decides what a busy send does) and queued bubbles show no
  // Send now link. Returns ok, like onSend.
  onSteer?: (text: string, attachments: Attachment[]) => Promise<boolean>;
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

// No `maxWidth` here (fix 4 review round 2): the three bubbles wrapped by
// withTimeLabel (queued-user, plain user, assistant) get their 80% cap from
// that wrapper's own style instead, because it is the wrapper — not the
// bubble — whose containing block is the transcript's definite-width column.
// Re-adding `maxWidth: '80%'` here for those bubbles would resolve against
// the wrapper's already-80%-capped width and compound to ~64%. The one
// remaining direct (unwrapped) consumer of bubbleBase, the centered
// System-message bubble below, sets its own `maxWidth: '80%'` explicitly.
const bubbleBase: React.CSSProperties = {
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

// Jagged bottom edge on an answer the operator interrupted: a zigzag mask, so
// it follows the bubble's own background and border in either theme.
const INTERRUPTED_STYLE_ID = 'optio-cc-interrupted-style';
function ensureInterruptedStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(INTERRUPTED_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = INTERRUPTED_STYLE_ID;
  el.textContent = `.optio-cc-interrupted {
    padding-bottom: 14px !important;
    -webkit-mask: conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) 50% / 12px 100%;
    mask: conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) 50% / 12px 100%;
  }`;
  document.head.appendChild(el);
}

// A vultus ActionStatus for the busy input bar's multi-action button. The
// view runs the (async) send itself, so both fire paths just start it.
function barAction(
  id: string,
  label: string,
  variant: 'primary' | 'default',
  disabled: boolean,
  run: () => void,
): ActionStatus {
  return { id, label, variant, pending: false, disabled, invisible: false, errors: [], fire: run, firePromise: async () => run() };
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

// Render a tool/permission's detail: the `input` KV table when it has fields,
// else the `content`-derived text `preview`. kimi/cursor permission cards and
// lazy/pending tool_calls carry NO rawInput — their detail lives only in the
// ACP `content` text (see acp/events.ts acpContentText), so without this the
// card shows just the tool name (the empty-card bug). Null when neither exists.
function renderDetail(
  input: unknown,
  preview: string | undefined,
  token: GlobalToken,
): React.ReactNode {
  const kv = renderInputKV(input, token);
  if (kv) return kv;
  if (preview) {
    return (
      <div style={{ fontFamily: 'monospace', fontSize: 12, color: token.colorTextSecondary, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {preview}
      </div>
    );
  }
  return null;
}

// Unified tool lifecycle across reducers that report completion differently:
//  - ACP (grok/cursor/kimi): the ChatItem `status` field (running|done|failed).
//  - codex: folds item/completed `status` + `exitCode` into `input`.
//  - antigravity (and any result-folding reducer): `input.result`.
// None present → still running (a just-announced tool). This is what lets the
// verbosity rules (hide-when-finished / collapse-when-finished) work uniformly
// for all 7 agents from the single shared render.
function toolLifecycle(item: Extract<ChatItem, { kind: 'tool' }>): { finished: boolean; failed: boolean } {
  if (item.status) return { finished: item.status !== 'running', failed: item.status === 'failed' };
  const input = item.input && typeof item.input === 'object' ? (item.input as Record<string, unknown>) : null;
  if (!input) return { finished: false, failed: false };
  const s = typeof input.status === 'string' ? input.status.toLowerCase() : '';
  const exit = input.exitCode;
  const failed = s === 'failed' || s === 'error' || (typeof exit === 'number' && exit !== 0);
  const finished = failed || s === 'completed' || s === 'success' || 'result' in input;
  return { finished, failed };
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

// The muted one-line outcome of a finished background job. The quiet levels
// (silent, description-while-active) render it instead of the tool row: the
// job ending is an event, not tool noise. An empty summary shows the tool name.
function renderBackgroundLine(
  item: Extract<ChatItem, { kind: 'tool' }>,
  failed: boolean,
  elapsed: string | null,
  token: GlobalToken,
): React.ReactNode {
  const stopped = item.status === 'stopped';
  const glyph = stopped ? '⏹' : failed ? '✗' : '✓';
  const label = stopped ? 'stopped' : failed ? 'failed' : 'finished';
  return (
    <div key={item.seq} data-testid="background-finished" style={{ color: token.colorTextTertiary, fontSize: 12 }}>
      {glyph} Background task {label}: {item.result || item.name}
      {elapsed ? ` · ${elapsed}` : ''}
    </div>
  );
}

// Capitalize a level label ("high" -> "High") for the segmented/slider marks.
function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Small grey time under a user/assistant/error bubble (Fix 4, owner ruling
// 2026-09-14: "all messages need a timestamp"). `now` is read once per render
// by the caller (formatMessageTime itself is pure, see messageTime.ts).
// Absent entirely when the item carries no known timestamp — never invented
// (e.g. a still-queued bubble in a replay, or any row that isn't a message).
function renderTimeLabel(timestamp: number | undefined, now: number, token: GlobalToken): React.ReactNode {
  if (timestamp === undefined) return null;
  return (
    <div
      data-testid="message-time"
      title={formatMessageTimeFull(timestamp)}
      style={{ fontSize: 11, color: token.colorTextTertiary, marginTop: 2 }}
    >
      {formatMessageTime(timestamp, now)}
    </div>
  );
}

// Fix 4 review round 1: the time label is a SIBLING below the bubble, never a
// descendant of it. Rendered inside the bubble it sat on the bubble's own
// tinted/coloured background (colorPrimaryBg, colorErrorBg, after the
// assistant cursor, between the queued text and its caption) with poor
// contrast. `align` mirrors the bubble's own alignSelf so the column takes
// the same place in the outer transcript flex column the bubble alone used
// to occupy; `labelAlign` right-aligns the label under user bubbles and
// left-aligns it under assistant and error bubbles. When `align` is
// 'flex-end'/'flex-start' (user, assistant) the column also gets an 80%
// width cap of its own: with the bubble now nested one level deeper, its
// containing block is this column, not the transcript, so the cap has to
// live here to resolve against the transcript's definite width. The error
// bubble stays 'stretch' (full width, as before) and is left uncapped.
//
// Fix 4 review round 2: this is now the ONLY 80% cap for the three bubbles
// that go through here (queued-user, plain user, assistant) — bubbleBase no
// longer sets `maxWidth` itself (see its definition). Do not re-add
// `maxWidth: '80%'` to bubbleBase and leave this one in place at the same
// time: the bubble's copy would then resolve against this column's
// already-80%-capped width, compounding to ~64% instead of 80% (invisible in
// jsdom, since it doesn't do CSS layout/percentage resolution).
function withTimeLabel(
  key: number,
  align: 'flex-end' | 'flex-start' | 'stretch',
  labelAlign: 'flex-end' | 'flex-start',
  bubble: React.ReactNode,
  timestamp: number | undefined,
  now: number,
  token: GlobalToken,
): React.ReactNode {
  return (
    <div
      key={key}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignSelf: align,
        alignItems: labelAlign,
        ...(align === 'stretch' ? null : { maxWidth: '80%' }),
      }}
    >
      {bubble}
      {renderTimeLabel(timestamp, now, token)}
    </div>
  );
}

// Generic renderer for engine-neutral session controls. Each control renders by
// kind: boolean -> <Switch>, segmented -> <Segmented>, slider -> <Slider>,
// select -> <Select> (disabled options greyed with a whyDisabled tooltip
// title). Every control carries a `control-<id>` data-testid.
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
        // A control the engine marked unchangeable (e.g. a select/segmented
        // collapsed to one option) is grayed and explains itself on hover.
        const dis = disabled || Boolean(c.disabled);
        let node: React.ReactNode;
        if (c.kind === 'boolean') {
          node = (
            <Switch
              data-testid={`control-${c.id}`}
              size="small"
              checked={Boolean(c.value)}
              disabled={dis}
              onChange={(v) => onChange(c.id, v)}
            />
          );
        } else if (c.kind === 'segmented') {
          node = (
            <Segmented
              data-testid={`control-${c.id}`}
              size="small"
              value={String(c.value)}
              disabled={dis}
              options={(c.levels ?? []).map((l) => ({
                label: l.charAt(0).toUpperCase() + l.slice(1),
                value: l,
              }))}
              onChange={(v) => onChange(c.id, String(v))}
            />
          );
        } else if (c.kind === 'slider') {
          const levels = c.levels ?? [];
          const idx = Math.max(0, levels.indexOf(String(c.value)));
          // antd's Slider (rc-slider) swallows data-testid rather than placing
          // it on the rendered root, so hang the control-<id> testid on a
          // wrapping span; the slider's disabled/handle state lives on the
          // .ant-slider inside it.
          node = (
            <span
              data-testid={`control-${c.id}`}
              style={{ display: 'inline-flex', minWidth: 160, alignSelf: 'center', marginLeft: 10 }}
            >
              <Slider
                style={{ flex: 1 }}
                min={0} max={Math.max(0, levels.length - 1)} step={null}
                marks={Object.fromEntries(
                  levels.map((l, i) => [
                    i,
                    { style: { fontSize: 10, whiteSpace: 'nowrap' }, label: capitalize(l) },
                  ]),
                )}
                value={idx} disabled={dis}
                onChange={(v: number) => onChange(c.id, levels[v])}
              />
            </span>
          );
        } else {
          node = (
            <Select
              data-testid={`control-${c.id}`}
              size="small"
              style={{ minWidth: 180, alignSelf: 'center' }}
              placeholder={c.label}
              disabled={dis}
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
        }
        // Prefix each control with its (muted) label so "Thinking"/"Mode" are
        // named — a bare Select/Segmented/Switch shows only its value.
        const labeled = (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 12, opacity: 0.65, whiteSpace: 'nowrap' }}>
              {c.label}
            </span>
            {node}
          </span>
        );
        // A disabled antd control emits no hover events, so hang the tooltip on
        // the (enabled) labeled wrapper — hovering the label/name still fires.
        return c.disabled && c.whyDisabled ? (
          <Tooltip key={c.id} title={c.whyDisabled}>
            {labeled}
          </Tooltip>
        ) : (
          <span key={c.id}>{labeled}</span>
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
    nativeSpinner,
    onSend,
    onInterrupt,
    onPermission,
    onFileDownload,
  } = props;

  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [wide, setWide] = useState(false);
  // Verbose mode collapses a FINISHED tool to its one-line summary; the seqs in
  // here are the finished tools the operator re-expanded (click to toggle).
  const [expandedTools, setExpandedTools] = useState<Set<number>>(() => new Set());
  const toggleTool = (seq: number) =>
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });

  // The wall clock for message-time formatting (Fix 4): read fresh at each
  // render — the formatter itself is pure and never reads the clock (see
  // messageTime.ts). Distinct from the ticking `now` below (which only
  // updates while a timed tool row is running): a message time only needs to
  // be current as of render, not to tick.
  const renderedAt = Date.now();

  // Live elapsed counters: tick once a second while any timed tool row is
  // still running (no endedAt); no interval otherwise.
  const [now, setNow] = useState(() => Date.now());
  const counting = state.items.some(
    (i) => i.kind === 'tool' && i.startedAt !== undefined && i.endedAt === undefined,
  );
  useEffect(() => {
    if (!counting) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [counting]);
  const inputRef = useRef<TextAreaRef>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const programmaticRef = useRef(false);
  const lastContentHeightRef = useRef(0);

  // Interrupt (Fix 6): a plain ref guards against a second click landing
  // before React re-renders (same reason sendingNowRef exists below), while
  // `interrupting` state drives the button's pending label/disabled look. A
  // void-returning onInterrupt (older engines) is treated as immediate
  // success — there is nothing to await.
  const interruptingRef = useRef(false);
  async function interrupt() {
    if (interruptingRef.current || !busy || closed) return;
    interruptingRef.current = true;
    setInterrupting(true);
    setError(null);
    try {
      const result = onInterrupt();
      const ok = result ? await result : true;
      if (!ok) setError('Interrupt failed — retry.');
    } catch {
      // A rejected onInterrupt is still "interrupt failed", not an unhandled
      // rejection (same guard as the Send now / onSteer path below).
      setError('Interrupt failed — retry.');
    } finally {
      interruptingRef.current = false;
      setInterrupting(false);
    }
  }

  // On mount: install the flash keyframes + copy hover rule and focus the input
  // so the operator can type immediately without clicking. The widget mounts
  // async (it un-gates only once widgetData arrives), so on a full page-load a
  // single focus() can land before the page settles and not stick — re-assert
  // it on short delays.
  useEffect(() => {
    ensureFlashStyle();
    ensureCopyStyle();
    ensureInterruptedStyle();
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
        void interrupt();
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

  // Steering applies only while a turn runs, and only for engines that wire it.
  const steerable = busy && !closed && props.onSteer !== undefined;

  // 'send' → onSend (Send; Send when ready while busy). 'steer' → onSteer
  // (Interrupt and send).
  async function submit(kind: 'send' | 'steer') {
    const body = text;
    if (!body || sending || closed) return;
    const deliver = kind === 'steer' ? props.onSteer : onSend;
    if (!deliver) return;
    setSending(true);
    setError(null);
    const ok = await deliver(body, attachments);
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

  function send() {
    return submit('send');
  }

  // Guards "Send now" on a queued bubble: reuses the same `sending` state the
  // busy bar uses (so a queued Send now and the bar's Send when ready /
  // Interrupt and send disable each other while either is in flight and share
  // the same error Alert), plus a plain ref so a second click landing before
  // React re-renders (no await between two fireEvent.click calls, e.g.) is
  // still dropped rather than firing a second onSteer('', []).
  const sendingNowRef = useRef(false);
  async function sendQueuedNow() {
    if (sendingNowRef.current || sending || closed || !props.onSteer) return;
    sendingNowRef.current = true;
    setSending(true);
    setError(null);
    try {
      const ok = await props.onSteer('', []);
      if (!ok) setError('Send failed — retry.');
    } catch {
      // onSteer rejecting is still "send failed", not an unhandled rejection.
      setError('Send failed — retry.');
    } finally {
      sendingNowRef.current = false;
      setSending(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      // Cmd/Ctrl-Enter interrupts and sends while the agent works; Enter
      // sends (when ready, if busy), and so does Cmd/Ctrl-Enter when idle.
      void submit((e.metaKey || e.ctrlKey) && steerable ? 'steer' : 'send');
    } else if (e.key === 'Escape' && busy && !closed) {
      // Same guard as the Interrupt button: only while a turn is running.
      e.preventDefault();
      void interrupt();
    }
  }

  function renderItem(item: ChatItem) {
    switch (item.kind) {
      case 'user':
        // A Send when ready the agent has not taken yet: user colours, dashed
        // and muted; the reducer keeps it pinned at the bottom.
        if (item.queued) {
          return withTimeLabel(
            item.seq,
            'flex-end',
            'flex-end',
            <div
              data-testid="queued-bubble"
              style={{
                ...bubbleBase,
                background: token.colorPrimaryBg,
                border: `1px dashed ${token.colorPrimaryBorder}`,
                borderRadius: '14px 14px 4px 14px',
                color: token.colorText,
                opacity: 0.6,
              }}
            >
              {item.text}
              <div style={{ fontSize: 12, color: token.colorTextSecondary, marginTop: 4, whiteSpace: 'normal' }}>
                Queued — the agent reads it when ready
                {steerable ? (
                  <>
                    {' · '}
                    <Button
                      type="link"
                      size="small"
                      data-testid="queued-send-now"
                      disabled={sending}
                      style={{ padding: 0, height: 'auto', fontSize: 12 }}
                      onClick={() => void sendQueuedNow()}
                    >
                      Send now
                    </Button>
                  </>
                ) : null}
              </div>
            </div>,
            item.timestamp,
            renderedAt,
            token,
          );
        }
        return withTimeLabel(
          item.seq,
          'flex-end',
          'flex-end',
          <div
            style={{
              ...bubbleBase,
              background: token.colorPrimaryBg,
              border: `1px solid ${token.colorPrimaryBorder}`,
              borderRadius: '14px 14px 4px 14px',
              // Explicit token color — without it the text inherits the host's
              // default and is unreadable on the dark-mode bubble.
              color: token.colorText,
            }}
          >
            {item.text}
          </div>,
          item.timestamp,
          renderedAt,
          token,
        );
      case 'assistant':
        return withTimeLabel(
          item.seq,
          'flex-start',
          'flex-start',
          <div
            // An answer the operator interrupted keeps its text and gets a
            // jagged bottom edge (the class is installed on mount).
            data-testid={item.interrupted ? 'answer-interrupted' : undefined}
            className={item.interrupted ? 'optio-cc-interrupted' : undefined}
            style={{
              ...bubbleBase,
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
          </div>,
          item.timestamp,
          renderedAt,
          token,
        );
      case 'activity':
        // A muted note ("⏹ Interrupted by you", an undelivered message): one
        // quiet centred line, not a bubble.
        if (item.muted) {
          return (
            <div
              key={item.seq}
              data-testid="activity-muted"
              style={{ alignSelf: 'center', color: token.colorTextTertiary, fontSize: 12 }}
            >
              {item.text}
            </div>
          );
        }
        // Harness System: messages — neither the user nor the agent, so render
        // a centered bubble in a distinct (lavender) colour, set apart from the
        // right-aligned user and left-aligned assistant bubbles.
        return (
          <div
            key={item.seq}
            style={{
              ...bubbleBase,
              // Not wrapped by withTimeLabel, so unlike the three bubbles
              // above this is the only place that needs to cap its own
              // width — bubbleBase no longer carries maxWidth (see its
              // definition).
              maxWidth: '80%',
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
        const { finished, failed } = toolLifecycle(item);
        const stopped = item.status === 'stopped';
        const elapsed =
          item.startedAt !== undefined ? formatDuration((item.endedAt ?? now) - item.startedAt) : null;
        if (toolVerbosity === 'silent' || toolVerbosity === 'description-while-active') {
          if (item.background && finished) return renderBackgroundLine(item, failed, elapsed, token);
          // silent: no other tool rows. description-while-active: only WHILE
          // the tool runs.
          if (toolVerbosity === 'silent' || finished) return null;
        }

        let summary = toolSummary(item.input);
        if (!summary && item.preview) summary = item.preview.split('\n')[0].slice(0, 120);
        const glyph = !finished ? '⟳' : stopped ? '⏹' : failed ? '✗' : '✓';

        // verbose shows the args/result detail; a FINISHED verbose tool collapses
        // to its line (click to re-expand). Non-verbose levels are line-only.
        const collapsible = toolVerbosity === 'verbose' && finished;
        const open = toolVerbosity === 'verbose' && (!finished || expandedTools.has(item.seq));
        return (
          <div key={item.seq} data-testid="tool-call" data-tool-status={finished ? (stopped ? 'stopped' : failed ? 'failed' : 'done') : 'running'}
               style={{ color: token.colorTextTertiary, fontSize: 12 }}>
            <div
              style={{ fontFamily: 'monospace', cursor: collapsible ? 'pointer' : 'default' }}
              onClick={collapsible ? () => toggleTool(item.seq) : undefined}
            >
              {glyph} <strong>{item.name}</strong>{summary ? `: ${summary}` : ''}
              {item.background && finished && item.result ? ` · ${item.result}` : ''}
              {elapsed ? <span data-testid="tool-elapsed">{` · ${elapsed}`}</span> : null}
              {collapsible ? <span style={{ marginLeft: 6 }}>{expandedTools.has(item.seq) ? '▾' : '▸'}</span> : null}
            </div>
            {open ? renderDetail(item.input, item.preview, token) : null}
            {open && item.result ? (
              <div
                data-testid="tool-result"
                style={{ fontFamily: 'monospace', fontSize: 12, color: token.colorTextSecondary, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', marginTop: 4 }}
              >
                {item.result}
              </div>
            ) : null}
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
            {renderDetail(item.input, item.preview, token)}
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
        return withTimeLabel(
          item.seq,
          'stretch',
          'flex-start',
          <div
            data-testid="conversation-error-item"
            style={{
              background: token.colorErrorBg,
              border: `1px solid ${token.colorErrorBorder}`,
              color: token.colorErrorText,
              borderRadius: 8,
              padding: '8px 12px',
              whiteSpace: 'pre-wrap',
            }}
          >
            {item.text}
          </div>,
          item.timestamp,
          renderedAt,
          token,
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
              {nativeSpinner ?? <Spin size="small" />} working…
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
            placeholder={
              steerable
                ? 'Message agent…  (Enter: send when ready, ⌘/Ctrl+Enter: interrupt and send, Shift+Enter: newline)'
                : 'Message agent…  (Enter to send, Shift+Enter for newline)'
            }
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
            {steerable ? (
              // Busy: one vultus multi-action button, [Send when ready |
              // Interrupt and send]. keepOriginalDefault keeps the main half
              // on Send when ready (so its width stays fixed) after the menu
              // action fires. The red Interrupt beside it stops and sends nothing.
              <span data-testid="conversation-send-combined">
                <CombinedActionButton
                  size="small"
                  keepOriginalDefault
                  actions={[
                    barAction('send-when-ready', 'Send when ready', 'primary', sending || !text, () => void submit('send')),
                    barAction('interrupt-and-send', 'Interrupt and send', 'default', sending || !text, () => void submit('steer')),
                  ]}
                />
              </span>
            ) : (
              <Button
                size="small"
                data-testid="conversation-send"
                type="primary"
                onClick={() => void send()}
                disabled={sending || !text || closed}
              >
                Send
              </Button>
            )}
            <Button
              size="small"
              danger
              data-testid="conversation-interrupt"
              disabled={!busy || closed || interrupting}
              onClick={() => void interrupt()}
            >
              {interrupting ? 'Interrupting…' : 'Interrupt'}
            </Button>
          </div>
          </div>
        </div>
      </div>
    </FileDownloadContext.Provider>
  );
}
