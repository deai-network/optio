// Pure event reducer: raw Antigravity transcript.jsonl events -> ChatState.
//
// All antigravity-specific interpretation lives here (testable without a DOM);
// the listener and the widget transport pass the objects through untouched.
//
// Antigravity has NO live transport (design §1) — no ACP, no stream-json, no
// HTTP. A "conversation" is SYNTHESISED from repeated one-shot `agy -p` turns
// plus the structured transcript file `~/.gemini/antigravity/transcript.jsonl`
// (optio-antigravity conversation.py). The listener tails that file and fans
// each new line out over SSE as the raw dict, unmodified.
//
// REAL transcript schema (captured from the real `agy` binary; fixture
// src/__tests__/fixtures/antigravity-real-transcript.jsonl). Each line is one
// JSON object; the load-bearing fields are `type` + `source`:
//   * USER_INPUT   (source USER_EXPLICIT) — the operator's message. `content`
//     is wrapped: "<USER_REQUEST>\n{text}\n</USER_REQUEST>\n<ADDITIONAL_META…".
//     We render ONLY the text between the USER_REQUEST tags (metadata dropped).
//   * PLANNER_RESPONSE (source MODEL) — the assistant. `content` is the answer
//     text (ABSENT/null when the step is only a tool call), `thinking` is the
//     reasoning string (optional), `tool_calls` is [{name, args}] (optional).
//     A turn emits its answer across SEVERAL PLANNER_RESPONSE lines (the real
//     "PONG" turn repeats the word before/after its tool calls), so the answer
//     COALESCES into a single per-turn bubble — the bubble text is the LAST
//     PLANNER_RESPONSE content of the turn (design §7: no token streaming, so a
//     content line is a whole statement, not a delta, and the last one wins).
//   * Tool-result lines (e.g. LIST_DIRECTORY, source MODEL, `content` = result)
//     fold their result into the preceding tool call's row (durable history).
//   * CHECKPOINT / CONVERSATION_HISTORY / GENERIC / SYSTEM_MESSAGE — bookkeeping
//     the operator does not need; ignored (and, crucially, they do NOT break the
//     per-turn answer coalescing above).
//
// Consequences pinned by design §7:
//   * NO token streaming — the coalesced PLANNER_RESPONSE content carries the
//     whole answer for a turn (there are no deltas to accumulate).
//   * `busy` clears when the model's answer lands (a PLANNER_RESPONSE with
//     non-empty content) and is re-armed by the next USER_INPUT / local echo.
//   * NO live permissions — turns run --dangerously-skip-permissions, so the
//     x-optio-permission-answered case is a parity seam that never fires.
// Tool calls are part of the durable transcript record (history), NOT ephemeral
// progress rows: the answer bubble does NOT drop them.

import type { ChatItem, ChatState } from '../chat.js';
import { foldControlUpdate } from '../chat.js';
import { parseUploadNotice, uploadNoticeActivityText } from '../uploads.js';
export { initialChatState } from '../chat.js';

// Tool rows persist as transcript history, so — unlike the streaming engines —
// the answer bubble must NOT strip them. There is no withoutTools() here.

// agy stores a tool call's args with human text in `toolAction`
// ("Listing directory contents") and `toolSummary` ("Directory listing"), both
// JSON-quoted (the value is itself wrapped in `"`), plus tool-specific keys
// (capitalized: DirectoryPath / AbsolutePath / Query / Command …). The shared
// tool renderer's summary only checks lowercase/generic keys, so it showed a
// bare "running <name>:" with no detail. Surface a clean `description` (which
// the shared renderer prefers) from toolAction → toolSummary.
function dequote(s: unknown): string {
  if (typeof s !== 'string') return '';
  const t = s.trim();
  return t.length >= 2 && t.startsWith('"') && t.endsWith('"') ? t.slice(1, -1) : t;
}
function normalizeToolInput(args: unknown): Record<string, unknown> {
  const a = (args && typeof args === 'object' && !Array.isArray(args))
    ? (args as Record<string, unknown>) : {};
  const description = dequote(a.toolAction) || dequote(a.toolSummary);
  return description ? { description, ...a } : { ...a };
}

// agy links deliverables in its answer as `[name](file:///abs/path)` markdown —
// NOT the optio-file: sentinel — and react-markdown strips the file:// scheme so
// the link isn't even clickable. Rewrite the link scheme to optio-file: so the
// shared Markdown renderer turns it into a /download click; the download reader
// confines the (absolute, in-workdir) path.
function rewriteFileLinks(content: string): string {
  return content.replace(/\]\(\s*file:\/\//g, '](optio-file:');
}

// Pull the operator's actual request out of a USER_INPUT `content` blob. The
// real transcript wraps it as "<USER_REQUEST>\n{text}\n</USER_REQUEST>\n<…meta>";
// we show only {text}. Absent tags (defensive) → the raw content, trimmed.
// Harness-injected messages (resume notices, auto-start prompt) carry this
// prefix; they render as activity rows, never user bubbles.
const HARNESS_PREFIX = 'System: ';

// Pull the operator's request out of a USER_INPUT `content` blob AND split off
// any `System: upload received…` notice lines an upload prepended (they land
// INSIDE the USER_INPUT). Returns the clean `.text` (the user bubble must show
// only the real request, and dedupe against the optimistic echo) and the
// captured `.uploads` (which drive a persistent muted "attached files" row that
// re-renders on transcript resume).
function extractUserRequest(content: unknown): { text: string; uploads: string[] } {
  if (typeof content !== 'string') return { text: '', uploads: [] };
  const m = content.match(/<USER_REQUEST>\s*([\s\S]*?)\s*<\/USER_REQUEST>/);
  const inner = (m ? m[1] : content).trim();
  const { text, uploads } = parseUploadNotice(inner);
  return { text: text.trim(), uploads };
}

// Coalesce a turn's answer into ONE assistant bubble. A turn is delimited by the
// most recent INPUT row; within it there is at most one assistant bubble, whose
// text is REPLACED by each new PLANNER_RESPONSE content (last content wins — the
// design pins no streaming, so a content line is a whole statement). Tool rows
// emitted between two content lines of the same turn do not fragment the answer.
//
// An input row is a `user` bubble OR a harness `System:` row (rendered as an
// `activity` row — antigravity's only source of activity rows): both trigger
// their own agy turn, so each opens a NEW answer bubble. Without treating the
// System row as a boundary, a resume-notice turn's reply would REPLACE the prior
// real question's reply in one bubble and drop the real answer.
function upsertTurnAnswer(items: ChatItem[], seq: number, text: string): ChatItem[] {
  let boundary = -1;
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].kind === 'user' || items[i].kind === 'activity') {
      boundary = i;
      break;
    }
  }
  for (let i = items.length - 1; i > boundary; i--) {
    if (items[i].kind === 'assistant') {
      const next: ChatItem = { ...items[i], text, pending: false } as ChatItem;
      const copy = [...items];
      copy[i] = next;
      return copy;
    }
  }
  return [...items, { kind: 'assistant', text, pending: false, seq, msgId: null }];
}

// A tool-result line (source MODEL, a type other than PLANNER_RESPONSE/GENERIC/
// CHECKPOINT — e.g. LIST_DIRECTORY) folds its output into the preceding tool
// call's row so a call + its result read as one durable history entry. With no
// preceding tool row it lands as a standalone result row.
function foldToolResult(state: ChatState, seq: number, type: unknown, content: string): ChatState {
  const last = state.items[state.items.length - 1];
  if (last && last.kind === 'tool') {
    const base =
      last.input && typeof last.input === 'object' && !Array.isArray(last.input)
        ? (last.input as Record<string, unknown>)
        : {};
    const next: ChatItem = { ...last, input: { ...base, result: content } };
    return { ...state, items: [...state.items.slice(0, -1), next] };
  }
  const name = typeof type === 'string' ? type.toLowerCase() : 'result';
  return { ...state, items: [...state.items, { kind: 'tool', name, input: { result: content }, seq }] };
}

export function reduceAntigravityEvent(state: ChatState, ev: any, seq: number): ChatState {
  const type = ev?.type;
  switch (type) {
    // Synthetic, widget-emitted: render the operator's own message the moment
    // the listener accepts it, before the transcript replays its USER_INPUT line.
    case 'x-optio-local-user': {
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      return {
        ...state,
        items: [...state.items, { kind: 'user', text, seq, local: true }],
        busy: true,
      };
    }

    case 'x-optio-local-error': {
      // A client-side upload failure the view surfaces immediately (transient —
      // not replayed on resume, unlike the successful-filename activity rows).
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      return { ...state, items: [...state.items, { kind: 'error', text, seq }] };
    }

    case 'USER_INPUT': {
      const { text, uploads } = extractUserRequest(ev.content);
      if (text === '' && uploads.length === 0) return state;
      const attach: ChatItem | null =
        uploads.length > 0 ? { kind: 'activity', text: uploadNoticeActivityText(uploads), seq } : null;
      // Harness-injected messages (resume notices, auto-start prompt) go through
      // the same send() path, so agy records them as USER_INPUT lines with a
      // "System: " prefix — render them as muted activity rows, never user
      // bubbles. An upload with no prompt body renders just its attachment row.
      if (text === '' || text.startsWith(HARNESS_PREFIX)) {
        let items = attach ? [...state.items, attach] : state.items;
        if (text !== '') items = [...items, { kind: 'activity', text, seq }];
        return { ...state, items, busy: true };
      }
      // Wire echo of an optimistically-rendered local message: confirm the
      // local bubble in place instead of inserting a duplicate (the attachment
      // row slots in just before it to stay chronological). FIFO by text — sends
      // are echoed in transcript order. (The local echo carries the raw typed
      // text; USER_INPUT carries the same text wrapped in USER_REQUEST, which
      // extractUserRequest unwraps back to it.)
      const localIdx = state.items.findIndex(
        (i) => i.kind === 'user' && i.local === true && i.text === text,
      );
      if (localIdx !== -1) {
        const confirmed = { ...state.items[localIdx] } as Extract<ChatItem, { kind: 'user' }>;
        delete confirmed.local;
        const items = attach
          ? [...state.items.slice(0, localIdx), attach, confirmed, ...state.items.slice(localIdx + 1)]
          : [...state.items.slice(0, localIdx), confirmed, ...state.items.slice(localIdx + 1)];
        return { ...state, items, busy: true };
      }
      // A user line the operator did not type through this widget (e.g. a replay
      // of history) opens the turn: attachment row then user bubble + busy.
      const appended = attach ? [...state.items, attach] : state.items;
      return { ...state, items: [...appended, { kind: 'user', text, seq }], busy: true };
    }

    case 'PLANNER_RESPONSE': {
      let items = state.items;
      // Reasoning first (the model thinks, then answers/acts). Its own kind so
      // the view can style it distinctly and gate it on thinkingVerbosity.
      if (typeof ev.thinking === 'string' && ev.thinking.trim() !== '') {
        items = [...items, { kind: 'thinking', text: ev.thinking, seq }];
      }
      // Answer content coalesces into the turn's single bubble (last wins).
      let busy = state.busy;
      if (typeof ev.content === 'string' && ev.content !== '') {
        items = upsertTurnAnswer(items, seq, rewriteFileLinks(ev.content));
        // The answer landing IS the turn end (no streaming, no turn-end frame).
        busy = false;
      }
      // Each tool call is durable transcript history — a KV-renderable row that
      // survives the answer bubble (the transcript is the source of truth).
      if (Array.isArray(ev.tool_calls)) {
        for (const tc of ev.tool_calls) {
          const name = String(tc?.name ?? 'tool');
          items = [...items, { kind: 'tool', name, input: normalizeToolInput(tc?.args), seq }];
        }
      }
      return items === state.items && busy === state.busy ? state : { ...state, items, busy };
    }

    // Bookkeeping lines the operator does not need. Ignored so they never
    // fragment the per-turn answer coalescing (design §7).
    case 'CONVERSATION_HISTORY':
    case 'CHECKPOINT':
    case 'GENERIC':
    case 'SYSTEM_MESSAGE':
      return state;

    case 'x-optio-control-update':
      // Session-control value change (the model picker). agy has no inline
      // switch — the next `agy -p` turn carries the new --model — so the only
      // source is the view's optimistic fold; keep state.controls in sync.
      return foldControlUpdate(state, ev);

    case 'x-optio-permission-answered': {
      // Parity seam only: antigravity turns run skip-permissions (design §7), so
      // no permission card is ever created and this never matches. Kept for
      // cross-engine symmetry.
      const requestId = String(ev.request_id);
      const behavior: 'allow' | 'deny' = ev.behavior === 'allow' ? 'allow' : 'deny';
      let changed = false;
      const items = state.items.map((item) => {
        if (item.kind !== 'permission' || item.requestId !== requestId || item.answered !== null) {
          return item;
        }
        changed = true;
        return { ...item, answered: behavior };
      });
      return changed ? { ...state, items } : state;
    }

    case 'x-optio-closed': {
      const item: ChatItem = { kind: 'closed', reason: String(ev.reason ?? ''), seq };
      return { ...state, items: [...state.items, item], busy: false, closed: true };
    }

    default:
      // A tool-result line (source MODEL, a non-PLANNER_RESPONSE/GENERIC/
      // CHECKPOINT type, e.g. LIST_DIRECTORY) folds its output into the
      // preceding tool call. Everything else (x-optio-unparseable, unknown
      // types) is a forward-compat no-op.
      if (ev?.source === 'MODEL' && typeof ev?.content === 'string' && ev.content !== '') {
        return foldToolResult(state, seq, type, ev.content);
      }
      return state;
  }
}
