import type { ChatItem, ChatState } from '../chat.js';

/** Reducer over opencode's native /global/event SSE frames.
 *  Each frame is `{directory?, project?, payload: {id, type, properties}}`
 *  (server.connected/heartbeat frames are payload-only; synthetic x-optio-*
 *  events arrive bare) — the reducer unwraps the envelope itself, so the
 *  transport passes frames through engine-native. Normalization to ChatItem
 *  happens here. */

/** Adapter-private memory threaded through the shared ChatState (structural
 *  superset — the extra fields ride along unseen by the generic widget):
 *  - roles: message role per messageID — parts/deltas don't carry the role,
 *    only the preceding message.updated does, and user text parts must render
 *    as user bubbles, not assistant ones.
 *  - partTypes: part type per partID — deltas stream the `text` field of
 *    `reasoning` parts too, and must not leak into the answer bubble.
 *  - userSeqs: rendered user item per messageID — makes the wire text part
 *    idempotent with the optimistic local echo (no duplicate bubble). */
interface OpencodeChatState extends ChatState {
  roles?: Record<string, string>;
  partTypes?: Record<string, string>;
  userSeqs?: Record<string, number>;
}

function sid(ev: any): string | undefined {
  const p = ev?.properties ?? {};
  return p.sessionID ?? p.info?.sessionID ?? p.part?.sessionID;
}

function upsertAssistant(
  items: ChatItem[], msgId: string, seq: number,
  update: (prev: { text: string; pending: boolean }) => { text: string; pending: boolean },
): ChatItem[] {
  const idx = items.findIndex((i) => i.kind === 'assistant' && i.msgId === msgId);
  if (idx === -1) {
    const fresh = update({ text: '', pending: true });
    return [...items, { kind: 'assistant', msgId, seq, ...fresh }];
  }
  const prev = items[idx] as Extract<ChatItem, { kind: 'assistant' }>;
  const next = { ...prev, ...update({ text: prev.text, pending: prev.pending }) };
  return [...items.slice(0, idx), next, ...items.slice(idx + 1)];
}

const dropTools = (items: ChatItem[]) => items.filter((i) => i.kind !== 'tool');

export function reduceOpencodeEvent(
  state: ChatState, ev: any, seq: number, sessionID: string,
): ChatState {
  return reduce(state as OpencodeChatState, ev, seq, sessionID);
}

function reduce(
  st: OpencodeChatState, ev: any, seq: number, sessionID: string,
): OpencodeChatState {
  // Unwrap the /global/event envelope; bare events (synthetic, fixtures'
  // distilled form, forward compat) pass through as-is.
  const frame = ev?.payload && typeof ev.payload === 'object' ? ev.payload : ev;
  const t = frame?.type as string | undefined;
  const p = frame?.properties ?? {};
  if (!t) return st;
  const evSid = sid(frame);
  if (evSid !== undefined && evSid !== sessionID) return st;

  // Synthetic, dispatched locally by the view on send (optimistic echo):
  if (t === 'x-optio-local-user') {
    return { ...st, busy: true, items: [...st.items, { kind: 'user', text: p.text ?? '', seq, local: true }] };
  }

  if (t === 'session.status' || t === 'session.idle') {
    const busy = t === 'session.status' && p.status?.type === 'busy';
    return { ...st, busy };
  }

  if (t === 'message.part.delta') {
    // Deltas stream a single field of one part; only the `text` field of a
    // part KNOWN to be a text part (or not yet announced — pure streaming)
    // belongs in the answer bubble. Reasoning parts stream `text` too.
    const partType = st.partTypes?.[String(p.partID ?? '')];
    if (partType !== undefined && partType !== 'text') return st;
    if (p.field !== undefined && p.field !== 'text') return st;
    const msgId = String(p.messageID ?? '');
    return { ...st, items: upsertAssistant(st.items, msgId, seq, (prev) => ({ text: prev.text + (p.delta ?? ''), pending: true })) };
  }

  if (t === 'message.part.updated') {
    const part = p.part ?? {};
    const msgId = String(part.messageID ?? '');
    const partTypes = part.id
      ? { ...st.partTypes, [String(part.id)]: String(part.type ?? '') }
      : st.partTypes;
    if (part.type === 'text') {
      if (st.roles?.[msgId] === 'user') {
        // The user prompt's own text part: fill the already-rendered bubble
        // (optimistic echo or an earlier part event) in place, else append.
        const at = st.userSeqs?.[msgId];
        const idx = at === undefined ? -1 : st.items.findIndex((i) => i.kind === 'user' && i.seq === at);
        if (idx !== -1) {
          const items = [...st.items];
          items[idx] = { ...(items[idx] as Extract<ChatItem, { kind: 'user' }>), text: part.text ?? '' };
          return { ...st, partTypes, items };
        }
        return {
          ...st, partTypes,
          items: [...st.items, { kind: 'user', text: part.text ?? '', seq }],
          userSeqs: { ...st.userSeqs, [msgId]: seq },
        };
      }
      return { ...st, partTypes, items: upsertAssistant(st.items, msgId, seq, () => ({ text: part.text ?? '', pending: true })) };
    }
    if (part.type === 'tool') {
      return {
        ...st, partTypes,
        items: [...dropTools(st.items), { kind: 'tool', name: part.tool ?? 'tool', input: part.state?.input ?? {}, seq }],
      };
    }
    // reasoning / step-start / step-finish / unknown: remember the type
    // (so their deltas are ignored), render nothing.
    return partTypes === st.partTypes ? st : { ...st, partTypes };
  }

  if (t === 'message.updated') {
    const info = p.info ?? {};
    const msgId = String(info.id ?? '');
    const roles = typeof info.role === 'string'
      ? { ...st.roles, [msgId]: info.role }
      : st.roles;
    if (info.role === 'user') {
      // The wire echo of a sent message: confirm the optimistic local bubble
      // in place (FIFO by presence of the `local` flag), claudecode parity.
      const idx = st.items.findIndex((i) => i.kind === 'user' && i.local);
      if (idx !== -1) {
        const items = [...st.items];
        const confirmed = { ...(items[idx] as Extract<ChatItem, { kind: 'user' }>), local: false };
        items[idx] = confirmed;
        return { ...st, roles, items, busy: true, userSeqs: { ...st.userSeqs, [msgId]: confirmed.seq } };
      }
      return { ...st, roles }; // user message text arrives via its own part events / history
    }
    if (info.role === 'assistant' && info.time?.completed) {
      // Finalize the bubble if one exists (tool-only / reasoning-only
      // assistant messages never opened one — don't render an empty bubble),
      // and clear the ephemeral tool rows either way: the turn is over.
      const hasBubble = st.items.some((i) => i.kind === 'assistant' && i.msgId === msgId);
      const items = dropTools(
        hasBubble
          ? upsertAssistant(st.items, msgId, seq, (prev) => ({ ...prev, pending: false }))
          : st.items,
      );
      return { ...st, roles, items };
    }
    return { ...st, roles };
  }

  if (t === 'permission.asked') {
    return {
      ...st,
      items: [...dropTools(st.items), {
        kind: 'permission', requestId: String(p.id ?? ''), toolName: String(p.permission ?? ''),
        input: p.metadata ?? {}, answered: null, seq,
      }],
    };
  }

  if (t === 'permission.replied') {
    const rid = String(p.requestID ?? '');
    const answered = p.reply === 'reject' ? 'deny' as const : 'allow' as const;
    return {
      ...st,
      items: st.items.map((i) =>
        i.kind === 'permission' && i.requestId === rid ? { ...i, answered } : i),
    };
  }

  // server.connected, server.heartbeat, sync, session.updated, session.diff…
  return st;
}

/** Map GET /session/:id/message ({info, parts}[]) to the initial ChatItem list. */
export function historyToChatItems(history: any[], sessionID: string): ChatItem[] {
  const items: ChatItem[] = [];
  let seq = -1_000_000; // history sorts before any live seq
  for (const entry of history ?? []) {
    const info = entry?.info ?? {};
    if (info.sessionID !== undefined && info.sessionID !== sessionID) continue;
    const parts = entry?.parts ?? [];
    if (info.role === 'user') {
      const text = parts.filter((p: any) => p.type === 'text').map((p: any) => p.text).join('\n');
      if (text) items.push({ kind: 'user', text, seq: seq++ });
      continue;
    }
    if (info.role === 'assistant') {
      const text = parts.filter((p: any) => p.type === 'text').map((p: any) => p.text).join('\n\n');
      if (text) items.push({ kind: 'assistant', text, pending: false, seq: seq++, msgId: String(info.id ?? '') });
    }
  }
  return items;
}
