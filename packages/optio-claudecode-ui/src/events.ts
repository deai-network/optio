// Pure event reducer: raw Claude Code stream-json events -> ChatState.
//
// All Claude-specific interpretation lives here (testable without DOM):
// the listener and the widget transport pass raw events through untouched.
// Wire shapes per the Phase I conversation-gate design (system / user /
// assistant / result / control_request / x-optio-* synthetic events;
// partials arrive as {type:"stream_event", event:{...content_block_delta}}).

export type ChatItem =
  | { kind: 'user'; text: string; seq: number }
  | { kind: 'assistant'; text: string; pending: boolean; seq: number; msgId: string | null }
  | { kind: 'activity'; text: string; seq: number }
  | { kind: 'tool'; name: string; input: unknown; seq: number }
  | {
      kind: 'permission';
      requestId: string;
      toolName: string;
      input: unknown;
      answered: 'allow' | 'deny' | null;
      seq: number;
    }
  | { kind: 'closed'; reason: string; seq: number };

export interface ChatState {
  items: ChatItem[];
  busy: boolean;
  closed: boolean;
}

export const initialChatState: ChatState = {
  items: [],
  busy: false,
  closed: false,
};

const HARNESS_PREFIX = 'System: ';

// message.content is either a plain string or an array of content blocks;
// concatenate the text blocks.
function extractText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter((block: any) => block?.type === 'text' && typeof block.text === 'string')
    .map((block: any) => block.text)
    .join('');
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
  const current = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
  if (!current.pending) return items;
  return [...items.slice(0, idx), { ...current, pending: false }, ...items.slice(idx + 1)];
}

// Upsert the in-flight assistant bubble: replace (or append to) its text,
// creating the bubble if absent. Invariant kept: a pending bubble only
// absorbs text while it is the tail AND belongs to the same assistant
// message — otherwise it is finalized where it stands and a fresh pending
// bubble opens at the end. (The full-text `replace` dedups accumulated
// stream deltas WITHIN one message; across messages it would swallow
// earlier replies.)
function upsertPending(
  items: ChatItem[],
  seq: number,
  text: string,
  mode: 'replace' | 'append',
  msgId?: string,
): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1) {
    const current = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
    const sameMessage =
      mode === 'append' || current.msgId === null || msgId == null || current.msgId === msgId;
    if (isTail(items, idx) && sameMessage) {
      const next: ChatItem = {
        ...current,
        text: mode === 'append' ? current.text + text : text,
        msgId: msgId ?? current.msgId,
      };
      return [...items.slice(0, idx), next, ...items.slice(idx + 1)];
    }
    items = finalizeAt(items, idx);
  }
  return [...items, { kind: 'assistant', text, pending: true, seq, msgId: msgId ?? null }];
}

// Finalize the in-flight assistant bubble (pending -> false), replacing its
// text when the result carries one. Creates a finalized bubble if there is
// result text but no pending bubble (e.g. a replay that skipped partials).
function finalizePending(items: ChatItem[], seq: number, resultText: string | null): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1) {
    if (resultText === null || resultText === '') return items;
    return [...items, { kind: 'assistant', text: resultText, pending: false, seq, msgId: null }];
  }
  const current = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
  const next: ChatItem = {
    ...current,
    text: resultText !== null ? resultText : current.text,
    pending: false,
  };
  return [...items.slice(0, idx), next, ...items.slice(idx + 1)];
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

function insertUserBeforePending(items: ChatItem[], item: ChatItem): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1 || !isTail(items, idx)) return [...items, item];
  return [...items.slice(0, idx), item, ...items.slice(idx)];
}

export function reduceEvent(state: ChatState, ev: any, seq: number): ChatState {
  switch (ev?.type) {
    case 'user': {
      const text = extractText(ev.message?.content);
      if (text === '') return state;
      // Harness-injected messages (resume notices, auto-start prompt) render
      // as activity rows, not user bubbles, and just append. Either way the
      // agent is working.
      if (text.startsWith(HARNESS_PREFIX)) {
        return { ...state, items: [...state.items, { kind: 'activity', text, seq }], busy: true };
      }
      const items = insertUserBeforePending(state.items, { kind: 'user', text, seq });
      return { ...state, items, busy: true };
    }

    case 'assistant': {
      const blocks = Array.isArray(ev.message?.content) ? ev.message.content : [];
      const msgId = typeof ev.message?.id === 'string' ? ev.message.id : undefined;
      let items = state.items;
      for (const block of blocks) {
        if (block?.type === 'text' && typeof block.text === 'string') {
          // The agent is answering now — clear any in-flight tool announcement,
          // then replace the pending bubble's text (the event carries the full
          // text so far, so accumulated stream_event deltas aren't double-counted
          // — within one message; a different message id opens a new bubble).
          items = upsertPending(withoutTools(items), seq, block.text, 'replace', msgId);
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
      const delta = ev.event?.delta?.text;
      if (typeof delta !== 'string' || delta === '') return state;
      // The answer is streaming — clear any in-flight tool announcement.
      return { ...state, items: upsertPending(withoutTools(state.items), seq, delta, 'append') };
    }

    case 'result': {
      const resultText = typeof ev.result === 'string' ? ev.result : null;
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
