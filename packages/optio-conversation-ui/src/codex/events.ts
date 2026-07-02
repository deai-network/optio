// Pure event reducer: raw codex app-server JSON-RPC messages -> ChatState.
//
// The listener and the widget transport pass the objects through untouched;
// all codex-specific interpretation lives here (testable without a DOM).
// Wire shapes are pinned in optio-codex's conversation.py (codex-cli 0.142.5
// probe + generated schemas). The "jsonrpc" field is omitted on the wire:
//   * notifications — item/agentMessage/delta {itemId, delta},
//     item/reasoning/summaryTextDelta|textDelta {delta}, item/started /
//     item/completed {item:{type,…}}, turn/completed {turn:{status,error}},
//     error {error:{message}}.
//   * server requests (id + method) — item/commandExecution/requestApproval
//     {command, cwd, …} and item/fileChange/requestApproval {reason, …}; the
//     listener correlates the operator's answer by the JSON-RPC id.
//   * responses (id, no method) — turn/start ACKs and handshake results (no
//     rendering); error responses surface as error items.
//   * synthetic x-optio-* events (permission-answered / closed / local-user).

import type { ChatItem, ChatState } from '../chat.js';
import { explainApiError } from '../apiError.js';
export { initialChatState } from '../chat.js';

// Adapter-private memory threaded through the shared ChatState (structural
// superset — the extra fields ride along unseen by the generic widget):
//  - turn: monotonic turn counter, so each turn's cumulative answer bubble
//    carries a distinct synthetic msgId (one bubble coalesces a turn even
//    when codex splits it across several agentMessage items).
//  - toolSeqs: seq of the rendered tool row per item.id, so item/completed
//    can find and refresh the row it belongs to.
interface CodexChatState extends ChatState {
  turn?: number;
  toolSeqs?: Record<string, number>;
}

const PERMISSION_METHODS = new Set([
  'item/commandExecution/requestApproval',
  'item/fileChange/requestApproval',
]);

function pendingIndex(items: ChatItem[]): number {
  return items.findIndex((i) => i.kind === 'assistant' && i.pending);
}

// The pending bubble may keep absorbing the in-flight turn only while it is
// the tail (ephemeral tool rows don't count — they are dropped by the next
// text).
function isTail(items: ChatItem[], idx: number): boolean {
  return items.slice(idx + 1).every((i) => i.kind === 'tool');
}

const dropTools = (items: ChatItem[]) => items.filter((i) => i.kind !== 'tool');

// Append a text delta to the in-flight assistant bubble, creating it (with
// the current turn's synthetic msgId) if absent or no longer the tail.
function appendPending(items: ChatItem[], seq: number, text: string, msgId: string): ChatItem[] {
  const idx = pendingIndex(items);
  if (idx !== -1 && isTail(items, idx)) {
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

// Reasoning is never folded into the answer. Coalesce contiguous reasoning
// deltas into a single muted activity row.
function appendThought(items: ChatItem[], seq: number, text: string): ChatItem[] {
  const last = items[items.length - 1];
  if (last && last.kind === 'activity') {
    const next: ChatItem = { ...last, text: last.text + text };
    return [...items.slice(0, -1), next];
  }
  return [...items, { kind: 'activity', text, seq }];
}

// item.type -> tool-row shape (name + KV input for verbose rendering).
function toolRow(item: any): { name: string; input: unknown } | null {
  switch (item?.type) {
    case 'commandExecution':
      return { name: String(item.command ?? 'command'), input: { command: item.command, cwd: item.cwd } };
    case 'fileChange':
      return { name: 'file change', input: { changes: item.changes } };
    case 'mcpToolCall':
      return { name: `${item.server ?? 'mcp'}.${item.tool ?? 'tool'}`, input: item.arguments ?? {} };
    case 'webSearch':
      return { name: 'web search', input: { query: item.query } };
    default:
      return null; // agentMessage/reasoning/userMessage/… — not tool rows
  }
}

export function reduceCodexEvent(state: ChatState, ev: any, seq: number): ChatState {
  return reduce(state as CodexChatState, ev, seq);
}

function reduce(st: CodexChatState, ev: any, seq: number): CodexChatState {
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

  // Server -> client REQUEST we must answer: the permission gate. The
  // listener correlates the operator's reply by this JSON-RPC id.
  if (method !== undefined && hasId && PERMISSION_METHODS.has(method)) {
    const params = ev.params ?? {};
    const item: ChatItem = {
      kind: 'permission',
      requestId: String(ev.id),
      toolName:
        method === 'item/commandExecution/requestApproval'
          ? String(params.command ?? 'command execution')
          : 'file change',
      input: params,
      answered: null,
      seq,
    };
    // busy stays true — the agent is parked on the gate. The request
    // supersedes any in-flight tool announcement.
    return { ...st, busy: true, items: [...dropTools(st.items), item] };
  }
  if (method !== undefined && hasId) return st; // other server requests: engine answers -32601

  // Server -> client NOTIFICATIONS.
  if (method !== undefined) {
    const params = ev.params ?? {};
    const msgId = `turn-${st.turn ?? 0}`;

    if (method === 'item/agentMessage/delta') {
      const text = params.delta ?? '';
      if (text === '') return st;
      // The agent is answering now — clear any in-flight tool announcement.
      return { ...st, busy: true, items: appendPending(dropTools(st.items), seq, text, msgId) };
    }

    if (method === 'item/reasoning/summaryTextDelta' || method === 'item/reasoning/textDelta') {
      const text = params.delta ?? '';
      if (text === '') return st;
      return { ...st, busy: true, items: appendThought(st.items, seq, text) };
    }

    if (method === 'item/started') {
      const item = params.item ?? {};
      const row = toolRow(item);
      if (!row) return st;
      const id = String(item.id ?? '');
      const chat: ChatItem = { kind: 'tool', name: row.name, input: row.input, seq };
      // Tool rows are ephemeral only w.r.t. new text/permission/close (see the
      // Task-7 design note) — NOT w.r.t. each other. Concurrent codex items
      // (commandExecution + fileChange + webSearch …) each keep their own row.
      return {
        ...st, busy: true,
        items: [...st.items, chat],
        toolSeqs: { ...st.toolSeqs, [id]: seq },
      };
    }

    if (method === 'item/completed') {
      const item = params.item ?? {};
      if (item.type === 'agentMessage') {
        // The completed item's text is authoritative for the turn's bubble
        // when it is a pure upgrade of what the deltas built (heals replay
        // gaps in the common single-item case).
        const idx = pendingIndex(st.items);
        const full = String(item.text ?? '');
        if (idx !== -1 && full) {
          const cur = st.items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
          if (full.startsWith(cur.text) && full !== cur.text) {
            const items = [...st.items.slice(0, idx), { ...cur, text: full }, ...st.items.slice(idx + 1)];
            return { ...st, items };
          }
          return st;
        }
        if (full) {
          return { ...st, busy: true, items: appendPending(dropTools(st.items), seq, full, msgId) };
        }
        return st;
      }
      const row = toolRow(item);
      if (!row) return st;
      const id = String(item.id ?? '');
      const at = st.toolSeqs?.[id];
      const idx = at === undefined ? -1 : st.items.findIndex((i) => i.kind === 'tool' && i.seq === at);
      const finalInput = {
        ...(row.input as object),
        status: item.status,
        ...(item.exitCode !== undefined && item.exitCode !== null ? { exitCode: item.exitCode } : {}),
      };
      if (idx !== -1) {
        const cur = st.items[idx] as Extract<ChatItem, { kind: 'tool' }>;
        const next: ChatItem = { ...cur, name: row.name, input: { ...(cur.input as object), ...finalInput } };
        return { ...st, items: [...st.items.slice(0, idx), next, ...st.items.slice(idx + 1)] };
      }
      // Completion for an untracked item (e.g. replay gap): render it. Like
      // item/started, it coexists with other tool rows.
      const chat: ChatItem = { kind: 'tool', name: row.name, input: finalInput, seq };
      return {
        ...st, busy: true,
        items: [...st.items, chat],
        toolSeqs: { ...st.toolSeqs, [id]: seq },
      };
    }

    if (method === 'turn/completed') {
      const turn = params.turn ?? {};
      let items = finalizePending(dropTools(st.items));
      if (turn.status === 'failed') {
        const msg = explainApiError(String(turn.error?.message ?? 'turn failed'), null);
        items = [...items, { kind: 'error', text: msg, seq }];
      }
      // Turn complete — close the bubble and open the next turn's bubble id.
      return { ...st, items, busy: false, turn: (st.turn ?? 0) + 1 };
    }

    if (method === 'error') {
      const msg = explainApiError(String(params.error?.message ?? 'error'), null);
      // May precede a failed turn/completed (which clears busy); dedupe on
      // an identical trailing error to avoid a double row from that pair.
      const last = st.items[st.items.length - 1];
      if (last && last.kind === 'error' && last.text === msg) return st;
      return { ...st, items: [...dropTools(st.items), { kind: 'error', text: msg, seq }] };
    }

    // thread/started, turn/started, thread/tokenUsage/updated, plan/… — no
    // dedicated rendering; passed through as no-ops.
    return st;
  }

  // Response to one of our requests (id, no method): turn/start ACKs and
  // handshake results need no rendering; an error response surfaces.
  if (hasId) {
    if (ev.error) {
      const msg = explainApiError(String(ev.error?.message ?? JSON.stringify(ev.error)), null);
      return { ...st, busy: false, items: [...dropTools(st.items), { kind: 'error', text: msg, seq }] };
    }
    return st;
  }

  return st;
}
