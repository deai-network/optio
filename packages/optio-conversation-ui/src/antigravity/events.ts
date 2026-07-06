// Pure event reducer: raw Antigravity transcript.jsonl events -> ChatState.
//
// All antigravity-specific interpretation lives here (testable without a DOM);
// the listener and the widget transport pass the objects through untouched.
//
// Antigravity has NO live transport (design §1) — no ACP, no stream-json, no
// HTTP. A "conversation" is SYNTHESISED from repeated one-shot `agy -p` turns
// plus the structured transcript file `~/.gemini/antigravity/transcript.jsonl`
// (optio-antigravity conversation.py). The listener tails that file and fans
// each new line out over SSE as a raw dict. Consequences pinned by design §7:
//   * NO token streaming — one `assistant` transcript line carries the whole
//     coalesced answer for a turn (there are no deltas to accumulate).
//   * NO separate turn-end frame — the assistant line IS the turn end, so it
//     lands finalized (pending=false) and clears `busy` on arrival.
//   * NO live permissions — turns run --dangerously-skip-permissions, so the
//     x-optio-permission-answered case is a parity seam that never fires.
// Tool calls are part of the durable transcript record (history), NOT ephemeral
// progress rows: the answer bubble does NOT drop them.
//
// Transcript line shapes ({user,assistant,tool} + conversationId) track
// fake_agy.py's documented minimal schema.
// TODO(S3): reconcile field names + multi-line-per-turn coalescing with the
// real captured transcript fixture once the S3 spike runs (the reducer is
// deliberately defensive about tool field names in the meantime).

import type { ChatItem, ChatState } from '../chat.js';
import { foldControlUpdate } from '../chat.js';
export { initialChatState } from '../chat.js';

// Tool rows persist as transcript history, so — unlike the streaming engines —
// the answer bubble must NOT strip them. There is no withoutTools() here.

// A completed answer line coalesces with the immediately-preceding assistant
// bubble (defends against a real transcript splitting one turn's answer across
// several `assistant` lines — TODO(S3)); a `user`/`tool` row in between opens a
// fresh bubble for the next turn. Every assistant line lands finalized.
function appendAnswer(items: ChatItem[], seq: number, text: string): ChatItem[] {
  const last = items[items.length - 1];
  if (last && last.kind === 'assistant') {
    const next: ChatItem = { ...last, text: last.text + text, pending: false };
    return [...items.slice(0, -1), next];
  }
  return [...items, { kind: 'assistant', text, pending: false, seq, msgId: null }];
}

function toolName(ev: any): string {
  return String(ev.name ?? ev.tool ?? ev.toolName ?? 'tool');
}

function toolInput(ev: any): unknown {
  return ev.input ?? ev.args ?? ev.arguments ?? ev.parameters ?? {};
}

export function reduceAntigravityEvent(state: ChatState, ev: any, seq: number): ChatState {
  switch (ev?.type) {
    // Synthetic, widget-emitted: render the operator's own message the moment
    // the listener accepts it, before the transcript replays its `user` line.
    case 'x-optio-local-user': {
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      return {
        ...state,
        items: [...state.items, { kind: 'user', text, seq, local: true }],
        busy: true,
      };
    }

    case 'user': {
      const text = typeof ev.text === 'string' ? ev.text : '';
      if (text === '') return state;
      // Wire echo of an optimistically-rendered local message: confirm the
      // local bubble in place instead of inserting a duplicate. FIFO by text —
      // sends are echoed in transcript order.
      const localIdx = state.items.findIndex(
        (i) => i.kind === 'user' && i.local === true && i.text === text,
      );
      if (localIdx !== -1) {
        const confirmed = { ...state.items[localIdx] } as Extract<ChatItem, { kind: 'user' }>;
        delete confirmed.local;
        const items = [...state.items];
        items[localIdx] = confirmed;
        return { ...state, items, busy: true };
      }
      // A user line the operator did not type through this widget (e.g. a resume
      // notice, or a replay of history) opens the turn: append + busy.
      return { ...state, items: [...state.items, { kind: 'user', text, seq }], busy: true };
    }

    case 'assistant': {
      const text = typeof ev.text === 'string' ? ev.text : '';
      // One assistant line = the whole turn's answer AND the turn end (no
      // streaming, no turn-end frame): land it finalized and clear busy.
      const items = text === '' ? state.items : appendAnswer(state.items, seq, text);
      return { ...state, items, busy: false };
    }

    case 'tool': {
      // Durable transcript history — a KV-renderable row that survives the
      // answer bubble (design: the transcript is the source of truth).
      return {
        ...state,
        items: [...state.items, { kind: 'tool', name: toolName(ev), input: toolInput(ev), seq }],
      };
    }

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
      // x-optio-unparseable, unknown transcript line types, etc.
      return state;
  }
}
