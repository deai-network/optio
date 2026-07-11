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
import { foldControlUpdate } from '../chat.js';
import { explainApiError } from '../apiError.js';
import { parseUploadNotice, uploadNoticeActivityText } from '../uploads.js';
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

// A tool is a HARD BOUNDARY, not a transient announcement: it finalizes the
// in-flight answer bubble and PERSISTS as a row (a later message opens a fresh
// bubble rather than coalescing across it). This mirrors the codex reducer
// (src/codex/events.ts) — the ACP forks previously `dropTools`'d the row and
// left the bubble pending, so post-tool text merged into the pre-tool bubble
// and the tool row vanished (the "bubble-collapse" bug). No `dropTools` helper:
// tool rows are conversation history, kept like messages.

// Extract a human-readable preview from an ACP `toolCall.content`
// (ToolCallContent[] — join the `text` parts). The detail lives here whenever
// `rawInput` is absent: kimi/cursor permission cards carry only `content`, and
// lazy/streaming `tool_call`s stream args as `content` text before `rawInput`
// arrives on dispatch. Empty string when there is no textual content.
function acpContentText(content: unknown): string {
  if (!Array.isArray(content)) return '';
  const parts: string[] = [];
  for (const c of content) {
    const inner = (c as { content?: { type?: string; text?: unknown } } | null)?.content;
    if (inner?.type === 'text' && typeof inner.text === 'string' && inner.text) {
      parts.push(inner.text);
    }
  }
  return parts.join('\n');
}

// Structured args when `rawInput` is a non-empty object (drives the KV table);
// null otherwise (the caller falls back to the `content` text preview).
function rawInputObject(raw: unknown): Record<string, unknown> | null {
  return raw && typeof raw === 'object' && !Array.isArray(raw) && Object.keys(raw).length > 0
    ? (raw as Record<string, unknown>)
    : null;
}

// Map the ACP tool `status` onto the ChatItem lifecycle. Now that tool rows
// PERSIST, a finished tool must not read "running" forever — the status drives
// the ⟳/✓/✗ glyph and the verbosity rules (description-while-active hides a
// finished tool; verbose collapses one). Unknown/absent → undefined so the
// caller keeps the prior value (or defaults to running on create).
function acpToolStatus(status: unknown): 'running' | 'done' | 'failed' | undefined {
  switch (status) {
    case 'pending':
    case 'in_progress':
      return 'running';
    case 'completed':
      return 'done';
    case 'failed':
    case 'error':
      return 'failed';
    default:
      return undefined;
  }
}

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

// Harness-injected messages (resume notices, upload notices) go through the same
// send() path, so the agent echoes them back as user_message_chunk too; they
// carry this prefix and render as muted activity rows, never user bubbles.
const HARNESS_PREFIX = 'System: ';

export function reduceAcpEvent(state: ChatState, ev: any, seq: number): ChatState {
  return reduce(state as AcpChatState, ev, seq);
}

function reduce(st: AcpChatState, ev: any, seq: number): AcpChatState {
  // Synthetic, widget/engine-emitted events (bare `type`, no JSON-RPC frame).
  const synthetic = ev?.type as string | undefined;
  if (synthetic === 'x-optio-control-update') {
    return foldControlUpdate(st, ev) as AcpChatState;
  }
  if (synthetic === 'x-optio-local-user') {
    const text = typeof ev.text === 'string' ? ev.text : '';
    if (text === '') return st;
    return { ...st, busy: true, items: [...st.items, { kind: 'user', text, seq, local: true }] };
  }
  if (synthetic === 'x-optio-local-error') {
    // A client-side upload failure the view surfaces immediately (transient —
    // not replayed on resume, unlike the successful-filename activity rows).
    const text = typeof ev.text === 'string' ? ev.text : '';
    if (text === '') return st;
    return { ...st, items: [...st.items, { kind: 'error', text, seq }] };
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
    return { ...st, items: [...st.items, item], busy: false, closed: true };
  }
  if (synthetic !== undefined) return st; // x-optio-unparseable, forward compat

  const method = ev?.method as string | undefined;
  const hasId = ev?.id !== undefined && ev?.id !== null;

  // Agent -> client REQUEST we must answer: session/request_permission. The
  // listener correlates the operator's reply by this JSON-RPC id.
  if (method === 'session/request_permission') {
    const toolCall = ev.params?.toolCall ?? {};
    // Detail source: the `rawInput` object (KV table) when present, else the
    // `content` text preview — kimi/cursor permission cards carry NO rawInput,
    // so without the preview the card shows only the tool name (the empty-card bug).
    const raw = rawInputObject(toolCall.rawInput);
    const item: ChatItem = {
      kind: 'permission',
      requestId: String(ev.id),
      toolName: String(toolCall.title ?? toolCall.kind ?? ''),
      input: raw ?? {},
      preview: raw ? undefined : acpContentText(toolCall.content) || undefined,
      answered: null,
      seq,
    };
    // busy stays true — the agent is parked on the gate. Finalize the answer
    // bubble (a tool boundary); tool rows persist as history.
    return { ...st, busy: true, items: [...finalizePending(st.items), item] };
  }

  // Agent -> client NOTIFICATION: session/update.
  if (method === 'session/update') {
    const update = ev.params?.update ?? {};
    const kind = update.sessionUpdate as string | undefined;
    const msgId = `turn-${st.turn ?? 0}`;

    if (kind === 'agent_message_chunk') {
      const text = update.content?.text ?? '';
      if (text === '') return st;
      // No dropTools: tool rows persist. Because `tool_call` finalizes the
      // prior bubble, this append opens a NEW bubble after a tool (the fixed
      // boundary) while still coalescing plain streamed text into one bubble.
      return { ...st, busy: true, items: appendPending(st.items, seq, text, msgId) };
    }

    if (kind === 'agent_thought_chunk') {
      const text = update.content?.text ?? '';
      if (text === '') return st;
      return { ...st, busy: true, items: appendThinking(st.items, seq, text) };
    }

    if (kind === 'tool_call') {
      const id = String(update.toolCallId ?? '');
      const raw = rawInputObject(update.rawInput);
      const item: ChatItem = {
        kind: 'tool',
        name: String(update.title ?? update.kind ?? 'tool'),
        input: raw ?? {},
        preview: raw ? undefined : acpContentText(update.content) || undefined,
        status: acpToolStatus(update.status) ?? 'running',
        seq,
      };
      // Tool boundary: finalize the answer bubble, keep prior tool rows.
      return {
        ...st, busy: true,
        items: [...finalizePending(st.items), item],
        toolSeqs: { ...st.toolSeqs, [id]: seq },
      };
    }

    if (kind === 'tool_call_update') {
      const id = String(update.toolCallId ?? '');
      const at = st.toolSeqs?.[id];
      const idx = at === undefined ? -1 : st.items.findIndex((i) => i.kind === 'tool' && i.seq === at);
      const raw = rawInputObject(update.rawInput);
      // Dispatch carries the real `rawInput` (→ KV table, drop the interim
      // content preview); a streaming delta refreshes the `content` preview.
      const preview = raw ? undefined : acpContentText(update.content) || undefined;
      if (idx !== -1) {
        const cur = st.items[idx] as Extract<ChatItem, { kind: 'tool' }>;
        const next: ChatItem = {
          ...cur,
          name: update.title !== undefined ? String(update.title) : cur.name,
          input: raw ?? cur.input,
          preview: raw ? undefined : (preview ?? cur.preview),
          status: acpToolStatus(update.status) ?? cur.status,
        };
        return { ...st, busy: true, items: [...st.items.slice(0, idx), next, ...st.items.slice(idx + 1)] };
      }
      // Update for an untracked tool (e.g. replay gap): render it as a row.
      const item: ChatItem = {
        kind: 'tool', name: String(update.title ?? update.kind ?? 'tool'),
        input: raw ?? {}, preview, status: acpToolStatus(update.status) ?? 'running', seq,
      };
      return {
        ...st, busy: true,
        items: [...finalizePending(st.items), item],
        toolSeqs: { ...st.toolSeqs, [id]: seq },
      };
    }

    if (kind === 'user_message_chunk') {
      // The agent emits the operator's prompt as user_message_chunk — during a
      // session/load REPLAY it is the ONLY per-turn delimiter (no session/prompt
      // turn-end arrives then). So each one BOTH renders the prompt AND opens a
      // new turn: finalize the prior answer bubble and bump the turn id so the
      // next agent_message_chunk starts a fresh bubble instead of coalescing
      // every replayed answer into one giant agent bubble (the resume bug —
      // merged answers, no prompts).
      // An upload prepends `System: upload received…` lines; split them off the
      // wire echo — `.text` is the real prompt (dedupes against the optimistic
      // echo), `.uploads` drives a persistent muted "attached files" row that
      // re-renders on resume (this same chunk replays during session/load).
      const { text, uploads } = parseUploadNotice(update.content?.text ?? '');
      if (text === '' && uploads.length === 0) return st;
      const base = finalizePending(st.items);
      const turn = (st.turn ?? 0) + 1;
      const attach: ChatItem | null =
        uploads.length > 0 ? { kind: 'activity', text: uploadNoticeActivityText(uploads), seq } : null;

      // Harness System: notice (resume/auto-start), or an upload with no prompt
      // body — render the attachment row (if any) then, for a harness message,
      // the muted System row. Never a user bubble.
      if (text === '' || text.startsWith(HARNESS_PREFIX)) {
        let items = attach ? [...base, attach] : base;
        if (text !== '') {
          const last = base[base.length - 1];
          if (!attach && last && last.kind === 'activity' && last.text === text) return st;
          items = [...items, { kind: 'activity', text, seq }];
        }
        return { ...st, items, turn };
      }
      // Real operator prompt: confirm the optimistic local echo in place (the
      // attachment row slots in just before it to stay chronological), else
      // append the attachment row then the user bubble.
      const idx = base.findIndex((i) => i.kind === 'user' && i.local && i.text === text);
      if (idx !== -1) {
        const cur = base[idx] as Extract<ChatItem, { kind: 'user' }>;
        const confirmed = { ...cur, local: false };
        const items = attach
          ? [...base.slice(0, idx), attach, confirmed, ...base.slice(idx + 1)]
          : [...base.slice(0, idx), confirmed, ...base.slice(idx + 1)];
        return { ...st, items, turn };
      }
      const appended = attach ? [...base, attach] : base;
      return { ...st, items: [...appended, { kind: 'user', text, seq }], turn };
    }

    // No-ops (no dedicated rendering):
    //  - plan / available_commands_update / session_info_update / _x.ai/*
    //  - grok (x.ai dialect): pending_interaction / interaction_resolved — its
    //    permission signal. In excavator analyze grok AUTO-approves, so these
    //    fire+resolve instantly with no gating (verified in the grok capture);
    //    no card is needed. grok's real GATED wire is not yet captured (manual
    //    mode) — if grok parks a pending_interaction rather than switching to
    //    session/request_permission, a card handler is added here then.
    //  - grok turn_completed: redundant with the session/prompt response that
    //    already drives turn-end below.
    return st;
  }

  // Response to one of our requests. The session/prompt response carries a
  // stopReason and IS the turn-end signal. A JSON-RPC error surfaces an error.
  if (hasId && method === undefined) {
    if (ev.error) {
      const msg = explainApiError(String(ev.error?.message ?? JSON.stringify(ev.error)), null);
      return { ...st, busy: false, items: [...st.items, { kind: 'error', text: msg, seq }] };
    }
    // session/new returns the unified configOptions. An EMPTY model picker means
    // the agent has no LLM configured (not logged in / stale credential): every
    // turn then fails SILENTLY (a bare stopReason:"end_turn", no content, no wire
    // error), so without this the operator sends a prompt and sees nothing.
    // Surface it once. Fires only on the specific empty-picker condition, so it
    // is safe for any ACP agent (was kimi-only before the fork was united).
    const configOptions = ev.result?.configOptions;
    if (Array.isArray(configOptions)) {
      const model = configOptions.find((o: any) => o?.category === 'model' || o?.id === 'model');
      if (model && Array.isArray(model.options) && model.options.length === 0) {
        const text =
          'No model is available — the agent is not logged in (its credential may be ' +
          'missing or expired). Prompts will not be answered until the login is refreshed.';
        if (st.items.some((i) => i.kind === 'error' && i.text === text)) return st;
        return { ...st, items: [...st.items, { kind: 'error', text, seq }] };
      }
    }
    if (ev.result && ev.result.stopReason !== undefined) {
      // Turn complete — finalize the answer bubble and open the next turn's
      // bubble id. Tool rows persist as conversation history.
      return {
        ...st,
        items: finalizePending(st.items),
        busy: false,
        turn: (st.turn ?? 0) + 1,
      };
    }
    return st; // handshake responses (initialize / session/new) — no rendering
  }

  return st;
}
