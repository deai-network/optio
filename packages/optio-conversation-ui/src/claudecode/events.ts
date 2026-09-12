// Pure event reducer: raw Claude Code stream-json events -> ChatState.
//
// All Claude-specific interpretation lives here (testable without DOM):
// the listener and the widget transport pass raw events through untouched.
// Wire shapes per the Phase I conversation-gate design (system / user /
// assistant / result / control_request / x-optio-* synthetic events;
// partials arrive as {type:"stream_event", event:{...content_block_delta}}).

import type { ChatItem, ChatState } from '../chat.js';
import { foldControlUpdate } from '../chat.js';
import { explainApiError } from '../apiError.js';
import { parseUploadNotice, uploadNoticeActivityText } from '../uploads.js';
export { initialChatState } from '../chat.js';

const HARNESS_PREFIX = 'System: ';

// Parts of one assistant message (narration, then the answer) render in one
// bubble, separated by a blank line.
const PART_SEPARATOR = '\n\n';

type AssistantItem = Extract<ChatItem, { kind: 'assistant' }>;

// Text a content block contributes to the reply bubble: a text block's text,
// or the narration a text-bearing thinking block carries. Since CLI 2.1.267
// the model writes its between-tool narration as such "thinking updates"; the
// real reasoning arrives as a separate thinking block with empty text.
function blockText(block: any): string | null {
  if (block?.type === 'text' && typeof block.text === 'string') return block.text;
  if (block?.type === 'thinking' && typeof block.thinking === 'string' && block.thinking.trim() !== '') {
    return block.thinking;
  }
  return null;
}

function replaceAt(items: ChatItem[], idx: number, item: ChatItem): ChatItem[] {
  return [...items.slice(0, idx), item, ...items.slice(idx + 1)];
}

// message.content is either a plain string or an array of content blocks;
// join the text blocks with a newline. Separate blocks are logically distinct
// messages (e.g. several harness "System:" notices claude coalesced into one
// user event) and must not render run-together.
function extractText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((block: any) => block?.type === 'text' && typeof block.text === 'string')
    .map((block: any) => block.text)
    .join('\n');
}

function pendingIndex(items: ChatItem[]): number {
  return items.findIndex((item) => item.kind === 'assistant' && item.pending);
}

// The pending bubble may keep absorbing the in-flight turn only while it is
// the conversation's tail. Ephemeral tool rows don't count: they are dropped
// by the next text anyway. Anything else after the bubble (activity rows,
// permission cards, user turns) means newer content has been appended — the
// bubble is stale and must not act as an anchor anymore.
function isTail(items: ChatItem[], idx: number): boolean {
  return items.slice(idx + 1).every((i) => i.kind === 'tool');
}

// Finalize the bubble at idx in place (text kept), used when newer content
// has to open a fresh bubble after it.
function finalizeAt(items: ChatItem[], idx: number): ChatItem[] {
  const current = items[idx] as AssistantItem;
  if (!current.pending) return items;
  const next: AssistantItem = { ...current, pending: false };
  delete next.openPart;
  return replaceAt(items, idx, next);
}

// A streamed delta (text_delta or thinking_delta) extends the open part of the
// pending bubble; the first delta of a block opens a new part, separated from
// earlier parts by a blank line. A pending bubble that is no longer the tail is
// finalized where it stands and a fresh bubble opens at the end.
function appendDelta(items: ChatItem[], seq: number, delta: string): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1 && isTail(items, idx)) {
    const cur = items[idx] as AssistantItem;
    if (cur.openPart !== undefined) return replaceAt(items, idx, { ...cur, text: cur.text + delta });
    const sep = cur.text === '' ? '' : PART_SEPARATOR;
    return replaceAt(items, idx, {
      ...cur,
      text: cur.text + sep + delta,
      openPart: cur.text.length + sep.length,
    });
  }
  if (idx !== -1) items = finalizeAt(items, idx);
  return [...items, { kind: 'assistant', text: delta, pending: true, seq, msgId: null, openPart: 0 }];
}

// A content block's final assistant event (it carries the block's full text).
// Within the same message it replaces the part its deltas streamed, or appends
// a new part when nothing streamed (replays hold no stream_events). A different
// message, or a pending bubble that is no longer the tail, opens a fresh bubble.
function applyBlockText(items: ChatItem[], seq: number, text: string, msgId?: string): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1) {
    const cur = items[idx] as AssistantItem;
    const sameMessage = cur.msgId === null || msgId == null || cur.msgId === msgId;
    if (isTail(items, idx) && sameMessage) {
      const base =
        cur.openPart !== undefined
          ? cur.text.slice(0, cur.openPart)
          : cur.text + (cur.text === '' ? '' : PART_SEPARATOR);
      const next: AssistantItem = { ...cur, text: base + text, msgId: msgId ?? cur.msgId };
      delete next.openPart;
      return replaceAt(items, idx, next);
    }
    items = finalizeAt(items, idx);
  }
  return [...items, { kind: 'assistant', text, pending: true, seq, msgId: msgId ?? null }];
}

// Finalize the in-flight assistant bubble (pending -> false). The result text
// replaces the bubble's text unless the bubble already ends with it: narration
// parts earlier in the same message must survive the end of the turn. Creates
// a finalized bubble if there is result text but no pending bubble (e.g. a
// replay that skipped partials).
function finalizePending(items: ChatItem[], seq: number, resultText: string | null): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1) {
    if (resultText === null || resultText === '') return items;
    return [...items, { kind: 'assistant', text: resultText, pending: false, seq, msgId: null }];
  }
  const current = items[idx] as AssistantItem;
  const keep = resultText === null || resultText === '' || current.text.endsWith(resultText);
  const next: AssistantItem = { ...current, text: keep ? current.text : resultText, pending: false };
  delete next.openPart;
  return replaceAt(items, idx, next);
}

// Insert a user message before the assistant bubble it triggered. With
// `--replay-user-messages` Claude streams the whole answer FIRST and only
// echoes the user message afterward, so the streaming assistant bubble already
// exists (and has an earlier seq) when the user echo arrives. Ordering by seq
// — or appending on arrival — would therefore render the answer above the
// question. Conversation order is what we want, so the echoed user turn slots
// in front of the in-flight assistant bubble — but ONLY while that bubble is
// the conversation's tail (modulo ephemeral tool rows). A stale pending
// bubble (e.g. replayed from a buffer captured mid-turn, never finalized)
// must not pull later, unrelated user events above newer content.
// Tool announcements are ephemeral progress indicators: only the in-flight one
// is interesting. A new tool announcement or a permission request supersedes
// any prior tool rows, so drop them when either arrives.
function withoutTools(items: ChatItem[]): ChatItem[] {
  return items.filter((i) => i.kind !== 'tool');
}

function insertBeforePending(items: ChatItem[], rows: ChatItem[]): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1 || !isTail(items, idx)) return [...items, ...rows];
  return [...items.slice(0, idx), ...rows, ...items.slice(idx)];
}

export function reduceEvent(state: ChatState, ev: any, seq: number): ChatState {
  // Sniff the runtime model and fold it into the model control. Claude Code
  // reports the model at top level on `system`/`init` (fires immediately at
  // launch) and on each assistant `message.model`. The stream uses the
  // runtime/variant id (e.g. claude-opus-4-8[1m]); strip the [..] suffix so it
  // matches a catalog option. Only fill while the control has no value yet — an
  // operator pick (reflected optimistically) must win, and the in-flight
  // process keeps reporting the OLD model until the restart completes.
  const rawModel = ev?.model ?? ev?.message?.model;
  if (typeof rawModel === 'string' && rawModel) {
    const modelCtrl = state.controls.find((c) => c.id === 'model');
    if (modelCtrl && !modelCtrl.value) {
      state = foldControlUpdate(state, { id: 'model', value: rawModel.replace(/\[[^\]]*\]$/, '') });
    }
  }
  switch (ev?.type) {
    case 'x-optio-control-update':
      return foldControlUpdate(state, ev);

    case 'user': {
      // An upload prepends `System: upload received…` lines; split them off —
      // `.text` is the real prompt, `.uploads` drives a persistent muted
      // "attached files" row that re-renders on resume (Claude replays the user
      // message under --replay-user-messages).
      const { text, uploads } = parseUploadNotice(extractText(ev.message?.content));
      if (text === '' && uploads.length === 0) return state;
      const attach: ChatItem | null =
        uploads.length > 0 ? { kind: 'activity', text: uploadNoticeActivityText(uploads), seq } : null;
      // Harness-injected messages (resume notices, auto-start prompt) render as
      // activity rows, not user bubbles; an upload with no prompt body renders
      // just its attachment row. Either way the agent is working.
      if (text === '' || text.startsWith(HARNESS_PREFIX)) {
        let items = attach ? [...state.items, attach] : state.items;
        if (text !== '') items = [...items, { kind: 'activity', text, seq }];
        return { ...state, items, busy: true };
      }
      // Wire echo of an optimistically-rendered local message (sent from this
      // widget): confirm the local bubble in place instead of inserting a
      // duplicate (the attachment row slots just before it). FIFO by text.
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
      // Replayed / un-echoed prompt: slot the attachment row + user bubble in
      // front of the in-flight assistant bubble (the answer streams first).
      const rows: ChatItem[] = attach
        ? [attach, { kind: 'user', text, seq }]
        : [{ kind: 'user', text, seq }];
      return { ...state, items: insertBeforePending(state.items, rows), busy: true };
    }

    // Synthetic, widget-emitted: render the operator's own message the moment
    // it is accepted by the listener, instead of waiting for Claude's echo
    // (which only arrives after the answer starts streaming).
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

    case 'assistant': {
      const blocks = Array.isArray(ev.message?.content) ? ev.message.content : [];
      const msgId = typeof ev.message?.id === 'string' ? ev.message.id : undefined;
      let items = state.items;
      for (const block of blocks) {
        const text = blockText(block);
        if (text !== null) {
          // The agent is answering (or narrating) — clear any in-flight tool
          // announcement, then complete this block's part of the bubble.
          items = applyBlockText(withoutTools(items), seq, text, msgId);
        } else if (block?.type === 'tool_use') {
          // Carry the structured input so the widget can render it as a
          // key→value table (same treatment as the permission card). Ephemeral:
          // supersede any prior tool announcement.
          items = [...withoutTools(items), { kind: 'tool', name: String(block.name ?? ''), input: block.input, seq }];
        }
      }
      return items === state.items ? state : { ...state, items };
    }

    case 'stream_event': {
      // A new assistant message announces itself before its deltas. Finalize
      // the previous message's bubble here, or its tail would absorb the next
      // message's deltas ("10" + "9" rendering as "109").
      if (ev.event?.type === 'message_start') {
        const idx = pendingIndex(state.items);
        return idx === -1 ? state : { ...state, items: finalizeAt(state.items, idx) };
      }
      // text_delta carries `text`; thinking_delta carries `thinking` (narration;
      // the hidden reasoning block streams no text). signature/input_json deltas
      // carry neither.
      const d = ev.event?.delta;
      const delta = d?.type === 'thinking_delta' ? d.thinking : d?.text;
      if (typeof delta !== 'string' || delta === '') return state;
      // The answer is streaming — clear any in-flight tool announcement.
      return { ...state, items: appendDelta(withoutTools(state.items), seq, delta) };
    }

    case 'result': {
      const resultText = typeof ev.result === 'string' ? ev.result : null;
      // An API/model error arrives as a result with is_error — surface it as a
      // distinct, explained error item instead of a plain agent bubble.
      if (ev.is_error) {
        const msg = explainApiError(resultText ?? '', ev.api_error_status);
        return {
          ...state,
          items: [...withoutTools(state.items), { kind: 'error', text: msg, seq }],
          busy: false,
        };
      }
      // Turn complete — drop any lingering tool announcement.
      return { ...state, items: finalizePending(withoutTools(state.items), seq, resultText), busy: false };
    }

    case 'control_request': {
      if (ev.request?.subtype !== 'can_use_tool') return state;
      const item: ChatItem = {
        kind: 'permission',
        requestId: String(ev.request_id),
        toolName: String(ev.request.tool_name ?? ''),
        input: ev.request.input,
        answered: null,
        seq,
      };
      // busy stays true — the agent is parked on the gate. The permission
      // request supersedes any in-flight tool announcement.
      return { ...state, items: [...withoutTools(state.items), item] };
    }

    case 'x-optio-permission-answered': {
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
      // Session ended — a trailing tool announcement (e.g. the agent echoing
      // DONE to optio.log) should not linger above the "conversation ended"
      // divider.
      const item: ChatItem = { kind: 'closed', reason: String(ev.reason ?? ''), seq };
      return { ...state, items: [...withoutTools(state.items), item], busy: false, closed: true };
    }

    default:
      // system, x-optio-unparseable, unknown control traffic, etc.
      return state;
  }
}
