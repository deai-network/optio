import type { ChatItem, ChatState } from '../chat.js';
import { foldControlUpdate } from '../chat.js';
import { explainApiError } from '../apiError.js';
import { parseUploadNotice, uploadNoticeActivityText } from '../uploads.js';

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

// Find the user/activity row a message's text part fills, matched by the seq
// stashed in userSeqs. Scans from the END so that when a persistent attachment
// row (spliced in just before the prompt row) happens to share the seq, the
// prompt row — appended after it — is the one selected, never the attachment row.
function findRowBySeq(items: ChatItem[], seq: number): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const k = items[i].kind;
    if ((k === 'user' || k === 'activity') && items[i].seq === seq) return i;
  }
  return -1;
}

// Harness-injected messages (resume notices, auto-start prompt) go through the
// same prompt path, so opencode records them as user-role messages carrying this
// prefix; they render as muted activity rows, never user bubbles (mirrors
// antigravity's HARNESS_PREFIX and the shared acp reducer's user_message_chunk).
const HARNESS_PREFIX = 'System: ';

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

  // Synthetic, dispatched locally by the view: a session-control change (the
  // model select is UI-local for opencode — applied inline on the next prompt,
  // no round-trip). A snapshot ({controls}) seeds/replaces, a patch ({id,value})
  // updates one control's value. frame carries controls/id/value at top level.
  if (t === 'x-optio-control-update') {
    return foldControlUpdate(st, frame) as OpencodeChatState;
  }

  // Synthetic, dispatched locally by the view on send (optimistic echo):
  if (t === 'x-optio-local-user') {
    return { ...st, busy: true, items: [...st.items, { kind: 'user', text: p.text ?? '', seq, local: true }] };
  }

  // Synthetic, dispatched locally by the view on a client-side upload failure —
  // surfaced immediately (transient; not replayed on resume, unlike the
  // successful-filename activity rows).
  if (t === 'x-optio-local-error') {
    const text = typeof p.text === 'string' ? p.text : '';
    if (text === '') return st;
    return { ...st, items: [...st.items, { kind: 'error', text, seq }] };
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
        // Split upload-notice lines off, then mute harness `System:` messages:
        // both ride the same user-role path but must render as activity rows,
        // never user bubbles. `.uploads` drives a persistent muted "attached
        // files" row (re-rendered on resume via historyToChatItems).
        const { text, uploads } = parseUploadNotice(part.text ?? '');
        const harness = text.startsWith(HARNESS_PREFIX);
        const attachText = uploads.length > 0 ? uploadNoticeActivityText(uploads) : null;
        // The user prompt's own text part: fill the already-rendered row
        // (optimistic echo or an earlier part event) in place, else append.
        const at = st.userSeqs?.[msgId];
        const idx = at === undefined ? -1 : findRowBySeq(st.items, at);
        if (idx !== -1) {
          const items = [...st.items];
          const existing = items[idx];
          items[idx] = existing.kind === 'activity'
            ? { ...existing, text }
            : { ...(existing as Extract<ChatItem, { kind: 'user' }>), text };
          // Splice the attachment row in just before the prompt row, once
          // (idempotent across repeat part.updated events for this part).
          if (attachText) {
            const prev = items[idx - 1];
            if (!(prev && prev.kind === 'activity' && prev.text === attachText)) {
              items.splice(idx, 0, { kind: 'activity', text: attachText, seq });
            }
          }
          return { ...st, partTypes, items };
        }
        const appended = [...st.items];
        if (attachText) appended.push({ kind: 'activity', text: attachText, seq });
        // An upload with no prompt body renders just the attachment row.
        if (text === '') return { ...st, partTypes, items: appended };
        appended.push(harness ? { kind: 'activity', text, seq } : { kind: 'user', text, seq });
        return {
          ...st, partTypes,
          items: appended,
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

  if (t === 'session.error') {
    // opencode surfaces a turn error; show it as a distinct, explained error
    // item. The error payload shape varies — probe the common locations.
    const err = p.error ?? {};
    const raw = String(err.message ?? err.data?.message ?? p.message ?? JSON.stringify(err) ?? '');
    const status = typeof err.status === 'number' ? err.status : null;
    return { ...st, busy: false, items: [...st.items, { kind: 'error', text: explainApiError(raw, status), seq }] };
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
      const raw = parts.filter((p: any) => p.type === 'text').map((p: any) => p.text).join('\n');
      // Same handling as the live path: split off upload notices (emitting the
      // persistent "attached files" row — the resume guarantee), and render a
      // harness `System:` message as an activity row (never a user bubble).
      const { text, uploads } = parseUploadNotice(raw);
      if (uploads.length > 0) {
        items.push({ kind: 'activity', text: uploadNoticeActivityText(uploads), seq: seq++ });
      }
      if (text) {
        items.push(text.startsWith(HARNESS_PREFIX)
          ? { kind: 'activity', text, seq: seq++ }
          : { kind: 'user', text, seq: seq++ });
      }
      continue;
    }
    if (info.role === 'assistant') {
      const text = parts.filter((p: any) => p.type === 'text').map((p: any) => p.text).join('\n\n');
      if (text) items.push({ kind: 'assistant', text, pending: false, seq: seq++, msgId: String(info.id ?? '') });
    }
  }
  return items;
}

/** A concrete opencode model selection, as accepted by prompt_async's `model`. */
export type OpencodeModel = { providerID: string; modelID: string };

/** Provider-grouped option model for the picker. */
export interface ModelGroup {
  providerName: string;
  models: { providerID: string; modelID: string; label: string }[];
}

/** Parse GET /config/providers into grouped options + a fallback default.
 *  Response shape (opencode 1.17.3-csillag.2):
 *    { providers: [{ id, name, models: { <modelId>: { id, providerID, name } } }],
 *      default: { <providerID>: <modelId> } }
 *  The default field maps each provider to its default model; we take the
 *  first provider's default as the widget-level fallback. */
export function parseProviders(json: any): { groups: ModelGroup[]; defaultModel: OpencodeModel | null } {
  const providers = Array.isArray(json?.providers) ? json.providers : [];
  const groups: ModelGroup[] = providers.map((p: any) => ({
    providerName: String(p?.name ?? p?.id ?? ''),
    models: Object.values(p?.models ?? {}).map((m: any) => ({
      providerID: String(m?.providerID ?? p?.id ?? ''),
      modelID: String(m?.id ?? ''),
      label: String(m?.name ?? m?.id ?? ''),
    })),
  }));
  let defaultModel: OpencodeModel | null = null;
  const first = providers[0];
  const def = json?.default;
  if (first && def && typeof def === 'object' && typeof def[first.id] === 'string') {
    defaultModel = { providerID: String(first.id), modelID: String(def[first.id]) };
  }
  return { groups, defaultModel };
}

/** The model of the last assistant message in GET /session/:id/message history,
 *  or null. Assistant `info` carries providerID/modelID (the datum the engine's
 *  _resolve_session_model_sync reads). */
export function lastModelFromHistory(history: any[]): OpencodeModel | null {
  let model: OpencodeModel | null = null;
  for (const entry of history ?? []) {
    const info = entry?.info ?? {};
    if (info.role === 'assistant' && info.providerID && info.modelID) {
      model = { providerID: String(info.providerID), modelID: String(info.modelID) };
    }
  }
  return model;
}
