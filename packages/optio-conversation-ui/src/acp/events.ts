// Pure event reducer: raw ACP (Agent Client Protocol, JSON-RPC 2.0)
// messages -> ChatState. Engine-neutral: grok and cursor both speak this
// public protocol, and their per-engine reducers (src/grok/events.ts,
// src/cursor/events.ts) are thin bindings over this single implementation
// (SSOT — extracted from src/grok/events.ts).
//
// The listener and the widget transport pass the ACP objects through
// untouched; all ACP interpretation lives here (testable without a DOM).
// Wire shapes are pinned in optio-grok's conversation.py (cursor implements
// the same protocol):
//   * session/update notification — params.update.sessionUpdate ∈
//       {agent_message_chunk, agent_thought_chunk, tool_call, tool_call_update}
//   * session/request_permission request — {id, params:{toolCall, options}}
//   * session/prompt response — {id, result:{stopReason}} == the turn-end
//   * synthetic x-optio-* events (permission-answered / closed / local-user).

import type { ChatItem, ChatState } from '../chat.js';
import { explainApiError } from '../apiError.js';
export { initialChatState } from '../chat.js';

// Adapter-private memory threaded through the shared ChatState (structural
// superset — the extra fields ride along unseen by the generic widget):
//  - turn: monotonic turn counter, so each turn's cumulative answer bubble
//    carries a distinct synthetic msgId (ACP chunks carry no message id).
//  - toolSeqs: seq of the rendered tool row per toolCallId, so tool_call_update
//    can find and refresh the row it belongs to.
interface AcpChatState extends ChatState {
  turn?: number;
  toolSeqs?: Record<string, number>;
}

function pendingIndex(items: ChatItem[]): number {
  return items.findIndex((i) => i.kind === 'assistant' && i.pending);
}

const dropTools = (items: ChatItem[]) => items.filter((i) => i.kind !== 'tool');

// Append a text delta to THIS turn's assistant bubble, matched by the turn's
// synthetic msgId — NOT by tail position. grok's reasoning models interleave
// agent_thought_chunk (rendered as distinct 'thinking' rows) WITH agent_message_chunk;
// a tail-position check would let each interleaved thought split the answer into
// a new bubble per run (the "tokens rendered separately" bug). Keying on msgId
// keeps the whole turn's answer in one bubble regardless of interleaving. A new
// turn (turn-end → turn++) yields a new msgId, so the next answer opens a fresh
// bubble; the prior turn's bubble is finalized (pending=false) and never matches.
function appendPending(items: ChatItem[], seq: number, text: string, msgId: string): ChatItem[] {
  const idx = items.findIndex(
    (i) => i.kind === 'assistant' && i.pending && i.msgId === msgId,
  );
  if (idx !== -1) {
    const cur = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
    const next: ChatItem = { ...cur, text: cur.text + text };
    return [...items.slice(0, idx), next, ...items.slice(idx + 1)];
  }
  return [...items, { kind: 'assistant', text, pending: true, seq, msgId }];
}

// Finalize the in-flight assistant bubble (pending -> false), if any.
function finalizePending(items: ChatItem[]): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx === -1) return items;
  const cur = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
  return [...items.slice(0, idx), { ...cur, pending: false }, ...items.slice(idx + 1)];
}

// agent_thought_chunk is REASONING — never folded into the answer, and NOT a
// harness System message (the 'activity' kind). It gets its own 'thinking' kind
// so the view can style it distinctly and gate it on thinkingVerbosity. Coalesce
// contiguous thinking chunks into one row (its deltas would otherwise spam one
// row per token).
function appendThinking(items: ChatItem[], seq: number, text: string): ChatItem[] {
  const last = items[items.length - 1];
  if (last && last.kind === 'thinking') {
    const next: ChatItem = { ...last, text: last.text + text };
    return [...items.slice(0, -1), next];
  }
  return [...items, { kind: 'thinking', text, seq }];
}

export function reduceAcpEvent(state: ChatState, ev: any, seq: number): ChatState {
  return reduce(state as AcpChatState, ev, seq);
}

function reduce(st: AcpChatState, ev: any, seq: number): AcpChatState {
  // Synthetic, widget/engine-emitted events (bare `type`, no JSON-RPC frame).
  const synthetic = ev?.type as string | undefined;
  if (synthetic === 'x-optio-local-user') {
    const text = typeof ev.text === 'string' ? ev.text : '';
    if (text === '') return st;
    return { ...st, busy: true, items: [...st.items, { kind: 'user', text, seq, local: true }] };
  }
  if (synthetic === 'x-optio-permission-answered') {
    const requestId = String(ev.request_id);
    const behavior: 'allow' | 'deny' = ev.behavior === 'allow' ? 'allow' : 'deny';
    let changed = false;
    const items = st.items.map((i) => {
      if (i.kind !== 'permission' || i.requestId !== requestId || i.answered !== null) return i;
      changed = true;
      return { ...i, answered: behavior };
    });
    return changed ? { ...st, items } : st;
  }
  if (synthetic === 'x-optio-closed') {
    const item: ChatItem = { kind: 'closed', reason: String(ev.reason ?? ''), seq };
    return { ...st, items: [...dropTools(st.items), item], busy: false, closed: true };
  }
  if (synthetic !== undefined) return st; // x-optio-unparseable, forward compat

  const method = ev?.method as string | undefined;
  const hasId = ev?.id !== undefined && ev?.id !== null;

  // Agent -> client REQUEST we must answer: session/request_permission. The
  // listener correlates the operator's reply by this JSON-RPC id.
  if (method === 'session/request_permission') {
    const toolCall = ev.params?.toolCall ?? {};
    const item: ChatItem = {
      kind: 'permission',
      requestId: String(ev.id),
      toolName: String(toolCall.title ?? toolCall.kind ?? ''),
      input: toolCall.rawInput ?? {},
      answered: null,
      seq,
    };
    // busy stays true — the agent is parked on the gate. The request supersedes
    // any in-flight tool announcement.
    return { ...st, busy: true, items: [...dropTools(st.items), item] };
  }

  // Agent -> client NOTIFICATION: session/update.
  if (method === 'session/update') {
    const update = ev.params?.update ?? {};
    const kind = update.sessionUpdate as string | undefined;
    const msgId = `turn-${st.turn ?? 0}`;

    if (kind === 'agent_message_chunk') {
      const text = update.content?.text ?? '';
      if (text === '') return st;
      // The agent is answering now — clear any in-flight tool announcement.
      return { ...st, busy: true, items: appendPending(dropTools(st.items), seq, text, msgId) };
    }

    if (kind === 'agent_thought_chunk') {
      const text = update.content?.text ?? '';
      if (text === '') return st;
      return { ...st, busy: true, items: appendThinking(st.items, seq, text) };
    }

    if (kind === 'tool_call') {
      const id = String(update.toolCallId ?? '');
      const item: ChatItem = {
        kind: 'tool',
        name: String(update.title ?? update.kind ?? 'tool'),
        input: update.rawInput ?? {},
        seq,
      };
      return {
        ...st, busy: true,
        items: [...dropTools(st.items), item],
        toolSeqs: { ...st.toolSeqs, [id]: seq },
      };
    }

    if (kind === 'tool_call_update') {
      const id = String(update.toolCallId ?? '');
      const at = st.toolSeqs?.[id];
      const idx = at === undefined ? -1 : st.items.findIndex((i) => i.kind === 'tool' && i.seq === at);
      if (idx !== -1) {
        const cur = st.items[idx] as Extract<ChatItem, { kind: 'tool' }>;
        const next: ChatItem = {
          ...cur,
          name: update.title !== undefined ? String(update.title) : cur.name,
          input: update.rawInput !== undefined ? update.rawInput : cur.input,
        };
        return { ...st, busy: true, items: [...st.items.slice(0, idx), next, ...st.items.slice(idx + 1)] };
      }
      // Update for an untracked tool (e.g. replay gap): render it as a row.
      const item: ChatItem = {
        kind: 'tool', name: String(update.title ?? update.kind ?? 'tool'),
        input: update.rawInput ?? {}, seq,
      };
      return {
        ...st, busy: true,
        items: [...dropTools(st.items), item],
        toolSeqs: { ...st.toolSeqs, [id]: seq },
      };
    }

    // plan / available_commands_update / user_message_chunk / _x.ai/* — no
    // dedicated rendering yet; passed through as no-ops.
    return st;
  }

  // Response to one of our requests. The session/prompt response carries a
  // stopReason and IS the turn-end signal. A JSON-RPC error surfaces an error.
  if (hasId && method === undefined) {
    if (ev.error) {
      const msg = explainApiError(String(ev.error?.message ?? JSON.stringify(ev.error)), null);
      return { ...st, busy: false, items: [...dropTools(st.items), { kind: 'error', text: msg, seq }] };
    }
    if (ev.result && ev.result.stopReason !== undefined) {
      // Turn complete — finalize the answer bubble, drop lingering tool rows,
      // and open the next turn's bubble id.
      return {
        ...st,
        items: finalizePending(dropTools(st.items)),
        busy: false,
        turn: (st.turn ?? 0) + 1,
      };
    }
    return st; // handshake responses (initialize / session/new) — no rendering
  }

  return st;
}
