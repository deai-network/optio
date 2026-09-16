import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Segmented, Select, Slider, Spin, Switch, Tooltip, theme } from 'antd';
import type { GlobalToken } from 'antd';
import { ActionButton, CombinedActionButton, type ActionStatus } from 'vultus-antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import type { ChatItem, ChatState, SessionControl } from './chat.js';
import { AnswerBlock } from './AnswerBlock.js';
import { type Attachment, toAttachment, withinCap } from './attachments.js';
import { FileDownloadContext } from './FileDownloadContext.js';
import { formatDuration } from './duration.js';
import {
  formatMessageTime, formatMessageTimeFull, formatMessageTimeInterval, formatMessageTimeIntervalFull,
} from './messageTime.js';

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
  // (onSteer('', [], upTo) — Fix 13b: `upTo` is that bubble's own id, so
  // Send now delivers up to (and including) it and leaves any later queued
  // bubbles queued, instead of "everything queued"). The bar's own Send when
  // ready / Interrupt and send calls pass no `upTo`. Absent: the bar keeps a
  // single Send while busy (the engine's /send decides what a busy send
  // does) and queued bubbles show no Send now link. Returns ok, like onSend.
  onSteer?: (text: string, attachments: Attachment[], upTo?: string) => Promise<boolean>;
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

// The user bubble's own corner treatment: full top corners, a flattened
// bottom-right one (its "tail", pointing at its own right-aligned time
// label). Named so it can be reused verbatim — Fix 14, owner ruling
// 2026-09-15, gives a "System: " activity row this same radius instead of a
// second copy of the shorthand.
const USER_BUBBLE_RADIUS = '14px 14px 4px 14px';

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

// Fix 16 (owner ruling 2026-09-15, manual-test finding): an interrupted
// answer's cut-off text (the jagged-edge bubble above) also trails off with
// an ellipsis, unless the text already ends with one. Render only: this
// computes what gets HANDED TO the markdown renderer, never item.text
// itself, so the reducer/stored-event text and the copy button (which reads
// item.text directly, below) are unaffected.
//
// Round 1 fix (review of the first cut): appending the ellipsis to the raw
// markdown SOURCE is only safe when the trimmed text ends inside an ordinary
// text run. When it ends on a line that *closes* a fenced code block or a
// display-math block, on a GFM table row, or on a bare URL/autolink, gluing
// "…" straight onto that line either reopens the construct (the fence/math
// delimiter line is no longer just delimiters, so the parser keeps reading
// content into a new line instead of closing) or silently changes rendered
// data (a new table cell; a link href that now points somewhere the model
// never produced). None of those are pre-existing degradation — they are
// regressions this appending scheme would introduce, so in those cases we
// leave the markdown source untouched and render the ellipsis as a sibling
// <span> right after the rendered answer block instead (the brief's
// explicitly allowed fallback for when inline placement "is not practical").
const ELLIPSIS = '…';

// Last non-empty line of the (already right-trimmed) text, e.g. the line
// that would carry a closing code fence, a closing "$$", or a table row.
function lastLine(trimmed: string): string {
  const lines = trimmed.split('\n');
  return lines[lines.length - 1];
}

// A line that is only a fenced-code delimiter (``` / ~~~, 3+ chars, optional
// trailing spaces). Appending "…" right after it — with no newline — turns
// it into e.g. "```…", which is no longer a valid fence line, so the parser
// no longer treats it as closing (or opening) a fence at all.
function isFenceDelimiterLine(line: string): boolean {
  return /^(`{3,}|~{3,})[ \t]*$/.test(line);
}

// A line that is only a display-math delimiter ("$$", or more dollars). Same
// problem as a fence: "$$…" is not a bare "$$" line any more.
function isMathDelimiterLine(line: string): boolean {
  return /^\${2,}[ \t]*$/.test(line);
}

// A GFM table row: starts and ends with "|". Appending "…" right after the
// trailing "|" is read as the start of one more cell in that row.
function isTableRowLine(line: string): boolean {
  return /^\|.*\|$/.test(line.trim());
}

// The very end of the text is a bare URL (GFM autolink extension) or an
// explicit <...> autolink. Appending "…" with no separating whitespace
// extends the link's own text/href to include it.
function endsWithAutolink(trimmed: string): boolean {
  const lastToken = /(\S+)$/.exec(trimmed)?.[1];
  if (!lastToken) return false;
  if (/^<[^\s<>]+>$/.test(lastToken)) return true;
  return /^(https?:\/\/|www\.)\S+$/i.test(lastToken);
}

// Whether appending "…" directly onto `trimmed` (no separating newline) is
// unsafe for any of the reasons above.
function unsafeToAppendInline(trimmed: string): boolean {
  const line = lastLine(trimmed);
  return isFenceDelimiterLine(line) || isMathDelimiterLine(line) || isTableRowLine(line) || endsWithAutolink(trimmed);
}

interface InterruptEllipsis {
  // What to hand to <AnswerBlock>. Never item.text itself when it differs —
  // callers must keep using item.text everywhere else (copy button, etc).
  markdownText: string;
  // When true, render a plain "…" as a sibling right after <AnswerBlock>
  // instead of it being part of the markdown source.
  trailingSpan: boolean;
}

function withInterruptEllipsis(text: string, interrupted: boolean | undefined): InterruptEllipsis {
  if (!interrupted) return { markdownText: text, trailingSpan: false };
  const trimmed = text.trimEnd();
  if (trimmed.endsWith(ELLIPSIS) || trimmed.endsWith('...')) return { markdownText: text, trailingSpan: false };
  if (unsafeToAppendInline(trimmed)) return { markdownText: text, trailingSpan: true };
  return { markdownText: trimmed + ELLIPSIS, trailingSpan: false };
}

// A vultus ActionStatus for the input bar's multi-action send button. The
// view runs the (async) send itself, so both fire paths just start it.
// Fix 22: `icon` is the Send/Send-when-ready/Interrupt-and-send mark shown on
// the right of the label (see sendBarIcon and friends above).
function barAction(
  id: string,
  label: string,
  variant: 'primary' | 'default',
  disabled: boolean,
  run: () => void,
  invisible = false,
  icon?: React.ReactNode,
): ActionStatus {
  return {
    id, label, variant, icon, pending: false, disabled, invisible, errors: [], fire: run, firePromise: async () => run(),
  };
}

// Fix 10 (owner feedback 2026-09-14): the send button used to swap between a
// plain Button (idle) and a CombinedActionButton (busy), so it changed size
// every time the agent went busy/idle. Now it is always the one
// CombinedActionButton — 'Send' visible only while idle, 'Send when ready' /
// 'Interrupt and send' visible only while steerable — and this fixed width
// keeps its footprint constant regardless of which caption is showing.
// Sized for the longest caption, the dropdown's main half reading
// "Interrupt and send" (19 characters) at size="small": antd's default
// 14px UI font sets small-button text around 145-155px for that string,
// plus ~14px of horizontal padding (7px each side) and a separate ~32px
// chevron half for the dropdown trigger. That was 200px before Fix 22.
// Fix 22 (owner ruling 2026-09-15): every main-half caption now also carries
// an icon on its right (iconPosition="end", Fix 21), so the longest state
// budgets for that too — an antd small-button icon is 14px square plus an
// 8px gap next to the label — adding 22px on top of the 200px above (not
// measured in a real browser here; computed from those two figures, which
// this file's own prior width comment and the Fix 22 brief both give).
// 222px keeps that a few px of headroom without leaving a visibly empty gap.
const SEND_BUTTON_WIDTH = 222;

// Makes the reserved SEND_BUTTON_WIDTH slot actually filled by the button
// rather than left-aligned inside empty space: the single-action case (a
// plain antd Button) already fills its parent at width:100%, and the
// dropdown-button case (Space.Compact, block=false so it does not fight
// other flex parents elsewhere) is told to fill this specific slot, with
// the main half absorbing the extra room and the chevron half staying its
// natural size.
const SEND_BUTTON_STYLE_ID = 'optio-cc-send-button-style';
function ensureSendButtonStyle(): void {
  if (typeof document === 'undefined' || document.getElementById(SEND_BUTTON_STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = SEND_BUTTON_STYLE_ID;
  el.textContent = `.optio-cc-send-btn { display: inline-flex; }
  .optio-cc-send-btn > .ant-btn { width: 100%; }
  .optio-cc-send-btn > .ant-dropdown-button { width: 100%; }
  .optio-cc-send-btn > .ant-dropdown-button > .ant-btn:first-child { flex: auto; }`;
  document.head.appendChild(el);
}

// Fix 20 (owner ruling 2026-09-15, manual-test finding): Fix 18 (677782b7..
// 9034cd89) gave Interrupt a fixed-width wrapper so antd's `loading` spinner
// wouldn't resize the button. That only worked in jsdom — in a real browser
// the button has no free room at rest, so the spinner still grew it and the
// whole input bar jumped; jsdom cannot measure layout, which is why the
// earlier review missed it. vultus's ActionStatus carries an `icon` field
// (vultus-core types.ts) that ActionButton passes straight to antd's Button,
// and antd renders its `loading` spinner IN THE ICON'S SLOT (button.js:
// `iconNode = icon && !innerLoading ? icon : … defaultLoadingIconElement()`)
// — so idle (icon + 'Interrupt') and pending (spinner + 'Interrupt') are the
// same width by construction, and no reserved width or extra CSS is needed.
// @ant-design/icons is not a dependency of optio-conversation-ui (checked
// package.json), so this is a small inline square in the '⏹' spirit rather
// than a library icon. The outer box is a fixed 1em x 1em — matching
// @ant-design/icons' IconBase, which sets width/height to '1em' on every
// antd icon including the LoadingOutlined spinner antd swaps in while
// pending — so the icon slot (.ant-btn-icon) is exactly as wide idle as it
// is pending. The visible 0.7em currentColor square is centered inside that
// fixed box; only the box needs to be measured or asserted on.
function InterruptStopIcon() {
  return (
    <span
      data-testid="interrupt-stop-icon"
      aria-hidden="true"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '1em',
        height: '1em',
      }}
    >
      <span
        style={{
          display: 'inline-block',
          width: '0.7em',
          height: '0.7em',
          backgroundColor: 'currentColor',
        }}
      />
    </span>
  );
}

// Fix 22 (owner rulings 2026-09-15, manual-test findings): every send-bar
// action gets an icon, not just Interrupt — Send a paper plane, Send when
// ready a clock, Interrupt and send a lightning bolt — and every icon (this
// trio plus Fix 20's InterruptStopIcon) sits on the RIGHT of its label via
// Fix 21's vultus `iconPosition="end"`. @ant-design/icons is still not a
// dependency of this package, so these are small inline SVGs rather than
// antd's SendOutlined/ClockCircleOutlined/ThunderboltOutlined, but they share
// InterruptStopIcon's outer shape: a fixed 1em x 1em box (matching
// @ant-design/icons' IconBase, and antd's LoadingOutlined spinner that takes
// this slot while an action is pending) with the visible mark centered
// inside — so idle (icon) and pending (spinner) stay the same width by
// construction, same as Interrupt. Menu rows are unaffected: `iconPosition`
// only ever reaches the main (button) half (see CombinedActionButton), so
// the dropdown menu keeps antd's own left-icon layout for its entries.
function sendBarIcon(testId: string, mark: React.ReactNode): React.ReactElement {
  return (
    <span
      data-testid={testId}
      aria-hidden="true"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '1em',
        height: '1em',
      }}
    >
      {mark}
    </span>
  );
}

function SendPlaneIcon() {
  return sendBarIcon(
    'send-icon',
    <svg viewBox="0 0 24 24" width="0.85em" height="0.85em" fill="currentColor">
      <path d="M2 21l21-9L2 3v7l15 2-15 2v7z" />
    </svg>,
  );
}

function SendWhenReadyClockIcon() {
  return sendBarIcon(
    'send-when-ready-icon',
    <svg viewBox="0 0 24 24" width="0.85em" height="0.85em" fill="none" stroke="currentColor" strokeWidth={2}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l4 2" />
    </svg>,
  );
}

function InterruptAndSendBoltIcon() {
  return sendBarIcon(
    'interrupt-and-send-icon',
    <svg viewBox="0 0 24 24" width="0.85em" height="0.85em" fill="currentColor">
      <path d="M13 2 3 14h7l-1 8 11-14h-7l1-6z" />
    </svg>,
  );
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

// Fix 12 (owner ruling 2026-09-14): the assistant-only variant of
// renderTimeLabel above, showing a "HH:MM - HH:MM" interval when the
// message's start and end fall in different local minutes (a single time
// otherwise), with the full start and end as the hover title. Driven by
// `end` (item.endTimestamp): absent entirely when there is no end, exactly
// like renderTimeLabel above — a bubble with no wire time yet (still
// streaming, or a replay that never saw one) shows no label at all, `start`
// notwithstanding. `start` (item.timestamp) is optional per chat.ts (the
// very first message of a conversation, replayed with no marker, may resolve
// no start): with none, the label and title fall back to the single end
// time, same as renderTimeLabel.
function renderTimeLabelRange(
  start: number | undefined, end: number | undefined, now: number, token: GlobalToken,
): React.ReactNode {
  if (end === undefined) return null;
  return (
    <div
      data-testid="message-time"
      title={formatMessageTimeIntervalFull(start, end)}
      style={{ fontSize: 11, color: token.colorTextTertiary, marginTop: 2 }}
    >
      {start === undefined ? formatMessageTime(end, now) : formatMessageTimeInterval(start, end, now)}
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
// `timeLabel` (Fix 12: was `timestamp`/`now`/`token`, computed internally via
// renderTimeLabel) is now a pre-rendered node, so each caller picks its own
// renderer: renderTimeLabel for a single time (queued/plain user, error), or
// renderTimeLabelRange for the assistant start-end interval. Passing a node
// rather than raw values also means a caller with no end (still streaming)
// renders nothing by simply passing renderTimeLabelRange's own null-when-no-
// end result, instead of this helper silently guessing which renderer to
// fall back to.
function withTimeLabel(
  key: number,
  align: 'flex-end' | 'flex-start' | 'stretch',
  labelAlign: 'flex-end' | 'flex-start',
  bubble: React.ReactNode,
  timeLabel: React.ReactNode,
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
      {timeLabel}
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
  // `interrupting` state drives the button's pending spinner/disabled look
  // (Fix 18: the label itself no longer changes). A void-returning
  // onInterrupt (older engines) is treated as immediate success — there is
  // nothing to await.
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

  // Fix 18 (owner addition 2026-09-15): every other input-bar button is now a
  // vultus action, so Interrupt becomes one too — a danger ActionStatus fed
  // to vultus's ActionButton, same shape as barAction above. `pending`
  // mirrors `interrupting`, so ActionButton's antd Button renders the
  // `loading` spinner (see ActionButton.tsx) instead of Fix 6's old
  // 'Interrupting…' label swap; `interrupt()` above still runs the same
  // ref-guarded fire-once/error logic, just invoked through `fire`.
  // Fix 20: `icon` gives idle Interrupt a stop icon; antd swaps it for the
  // loading spinner while `pending`, in the same slot (see InterruptStopIcon
  // above), which is what makes the fixed-width workaround unnecessary.
  const interruptAction: ActionStatus = {
    id: 'interrupt',
    label: 'Interrupt',
    icon: <InterruptStopIcon />,
    variant: 'danger',
    pending: interrupting,
    disabled: !busy || closed,
    invisible: false,
    errors: [],
    fire: () => void interrupt(),
    firePromise: async () => interrupt(),
  };

  // On mount: install the flash keyframes + copy hover rule and focus the input
  // so the operator can type immediately without clicking. The widget mounts
  // async (it un-gates only once widgetData arrives), so on a full page-load a
  // single focus() can land before the page settles and not stick — re-assert
  // it on short delays.
  useEffect(() => {
    ensureFlashStyle();
    ensureCopyStyle();
    ensureInterruptedStyle();
    ensureSendButtonStyle();
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

  // Fix 27 I4: two plain refs guarding the two ways text leaves the input —
  // `submittingRef` for submit() (Send / Send when ready / Interrupt and
  // send), `sendingNowRef` for sendQueuedNow() (a queued bubble's Send now).
  // Declared together and each checked by both functions, so a concurrent
  // Send-now and Enter (no await between two synchronous event handlers, so
  // the `sending` state update from one hasn't flushed when the other
  // reads it) can't clear each other's in-flight state.
  const submittingRef = useRef(false);
  const sendingNowRef = useRef(false);

  // 'send' → onSend (Send; Send when ready while busy). 'steer' → onSteer
  // (Interrupt and send). Fix 27 I4: this used to have no try/finally — a
  // rejecting deliver() (onSteer is a documented prop other engine views
  // can implement; ClaudeCodeView's own postJson/preparePrompt/uploadFiles
  // happen to catch everything, which is why this was latent) left
  // `sending` stuck true forever (the send bar, every Send now link and the
  // error Alert dead until remount) plus an unhandled promise rejection.
  async function submit(kind: 'send' | 'steer') {
    const body = text;
    if (!body || sending || sendingNowRef.current || submittingRef.current || closed) return;
    const deliver = kind === 'steer' ? props.onSteer : onSend;
    if (!deliver) return;
    submittingRef.current = true;
    setSending(true);
    setError(null);
    try {
      const ok = await deliver(body, attachments);
      if (ok) {
        setText('');
        setAttachments([]);
      } else {
        setError('Send failed — retry.');
      }
    } catch {
      // A rejected deliver() is still "send failed", not an unhandled
      // rejection (same guard as sendQueuedNow / interrupt above).
      setError('Send failed — retry.');
    } finally {
      submittingRef.current = false;
      setSending(false);
      // Keep the keyboard on the input so the operator can keep typing after
      // Enter without a mouse click.
      inputRef.current?.focus();
    }
  }

  // Guards "Send now" on a queued bubble: reuses the same `sending` state the
  // busy bar uses (so a queued Send now and the bar's Send when ready /
  // Interrupt and send disable each other while either is in flight and share
  // the same error Alert), plus a plain ref so a second click landing before
  // React re-renders (no await between two fireEvent.click calls, e.g.) is
  // still dropped rather than firing a second onSteer('', [], upTo). `upTo`
  // is the clicked bubble's own id, but since Fix 17 the native queue never
  // holds more than one message in flight, so it needs nothing beyond the
  // interrupt: Send now on any queued bubble does the same thing.
  async function sendQueuedNow(upTo?: string) {
    if (sendingNowRef.current || submittingRef.current || sending || closed || !props.onSteer) return;
    sendingNowRef.current = true;
    setSending(true);
    setError(null);
    try {
      const ok = await props.onSteer('', [], upTo);
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
                borderRadius: USER_BUBBLE_RADIUS,
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
                      onClick={() => void sendQueuedNow(item.queueId)}
                    >
                      Send now
                    </Button>
                  </>
                ) : null}
              </div>
            </div>,
            renderTimeLabel(item.timestamp, renderedAt, token),
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
              borderRadius: USER_BUBBLE_RADIUS,
              // Explicit token color — without it the text inherits the host's
              // default and is unreadable on the dark-mode bubble.
              color: token.colorText,
            }}
          >
            {item.text}
          </div>,
          renderTimeLabel(item.timestamp, renderedAt, token),
        );
      case 'assistant': {
        const ellipsis = withInterruptEllipsis(item.text, item.interrupted);
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
              <AnswerBlock text={ellipsis.markdownText} />
              {ellipsis.trailingSpan && <span data-testid="answer-interrupt-ellipsis">{ELLIPSIS}</span>}
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
          renderTimeLabelRange(item.timestamp, item.endTimestamp, renderedAt, token),
        );
      }
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
        // right-aligned user and left-aligned assistant bubbles. Fix 9, owner
        // ruling 2026-09-14: these rows show their time too, as a sibling
        // below the bubble (same small grey label as withTimeLabel's three
        // bubbles). Not routed through that helper (it is keyed to the
        // 'flex-end'/'flex-start'/'stretch' alignments the three message
        // kinds use), but built the same way it is: the wrapper div below is
        // this row's containing block, sized against the definite-width
        // transcript column, so IT carries the `maxWidth: '80%'` cap (fix 9
        // review round 1). The bubble itself must stay uncapped — capping
        // both would resolve the bubble's 80% against the wrapper's already
        // shrunk-to-fit width and compound far below 80%, wrapping short
        // text that fits on one line (invisible in jsdom, real in a
        // browser). This wrapper renders for every non-muted activity row
        // regardless of whether it has a timestamp; the cap living here
        // keeps that identical to today's width even when renderTimeLabel
        // below renders nothing.
        //
        // Fix 14, owner ruling 2026-09-15 (manual-test finding): a "System: "
        // row -- flagged `item.system` by the reducer, since an untimed
        // background-task/upload-notice row shares this same branch and a
        // timestamp alone can't tell them apart (chat.ts) -- is styled more
        // like a user message: the user bubble's own corner radius, and its
        // time label right-aligned like the one under a user bubble. Position
        // is otherwise unchanged: the bubble keeps its own `alignSelf:
        // 'center'` below regardless, so only the (narrower) label's place
        // within the column moves when `alignItems` turns 'flex-end'.
        return (
          <div
            key={item.seq}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignSelf: 'center',
              alignItems: item.system ? 'flex-end' : 'center',
              maxWidth: '80%',
            }}
          >
            <div
              data-testid="activity-bubble"
              style={{
                ...bubbleBase,
                // No maxWidth here — the wrapper above caps the row's width
                // against the transcript column; see the comment there.
                alignSelf: 'center',
                background: token.purple1,
                border: `1px solid ${token.purple3}`,
                color: token.colorTextSecondary,
                fontSize: 12,
                borderRadius: item.system ? USER_BUBBLE_RADIUS : 14,
              }}
            >
              {item.text}
            </div>
            {renderTimeLabel(item.timestamp, renderedAt, token)}
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
          renderTimeLabel(item.timestamp, renderedAt, token),
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
            {/* Fix 10: always the one vultus multi-action button, never a
                separate plain Send button — idle shows only 'Send'; busy and
                steerable shows [Send when ready | Interrupt and send]. An
                engine without onSteer (steerable stays false even while
                busy) keeps showing plain 'Send' behaviour. Visibility
                toggles which actions CombinedActionButton renders — with one
                visible it renders a plain button, so idle and a non-steering
                engine look identical to before. keepOriginalDefault keeps
                the main half on 'Send when ready' after the menu action
                fires, and back on 'Send' once idle. SEND_BUTTON_WIDTH pins
                the footprint so it never resizes across any of that; the red
                Interrupt beside it stops and sends nothing. Fix 22: each
                action's `icon` (plane / clock / bolt) plus iconPosition="end"
                puts that icon on the right of the label, on the main half
                only — the dropdown menu rows keep antd's own left-icon
                layout regardless. */}
            <span
              data-testid="conversation-send-combined"
              className="optio-cc-send-btn"
              style={{ width: SEND_BUTTON_WIDTH }}
            >
              <CombinedActionButton
                size="small"
                keepOriginalDefault
                iconPosition="end"
                actions={[
                  barAction('send', 'Send', 'primary', sending || !text || closed, () => void submit('send'), steerable, <SendPlaneIcon />),
                  barAction('send-when-ready', 'Send when ready', 'primary', sending || !text, () => void submit('send'), !steerable, <SendWhenReadyClockIcon />),
                  barAction('interrupt-and-send', 'Interrupt and send', 'default', sending || !text, () => void submit('steer'), !steerable, <InterruptAndSendBoltIcon />),
                ]}
              />
            </span>
            {/* Fix 18 (owner addition): Interrupt is now a vultus action
                (danger ActionStatus above) instead of a plain antd Button —
                it renders the same red button, but its own `pending` state
                drives ActionButton's antd `loading` spinner rather than
                swapping the label. Fix 20: the action's `icon` (a stop
                square) occupies the same slot antd's spinner takes over
                while pending, so the button's width no longer needs to be
                reserved — this wrapper only keeps the stable test id. Fix
                22: iconPosition="end" puts that icon on the right of the
                label, matching every other send-bar action. */}
            <span data-testid="conversation-interrupt">
              <ActionButton action={interruptAction} size="small" iconPosition="end" />
            </span>
          </div>
          </div>
        </div>
      </div>
    </FileDownloadContext.Provider>
  );
}
