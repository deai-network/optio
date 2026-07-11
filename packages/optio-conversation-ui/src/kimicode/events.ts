// Kimi binding of the engine-neutral ACP reducer (src/acp/events.ts) — kimi
// speaks stock ACP (JSON-RPC 2.0), so the binding is a pure re-export, same as
// grok and cursor. Wire shapes are pinned in optio-kimicode's conversation.py;
// the shared reducer's behavior is pinned by acp/grok/kimicode event tests.
//
// This was a 305-line FORK of the ACP reducer; it was united into the shared
// implementation (its only functional delta — the empty-model-picker
// "not logged in" error — now lives in the shared reducer, gated on the
// condition rather than the agent). See the reducer-unification work.
import type { ChatState } from '../chat.js';
import { reduceAcpEvent } from '../acp/events.js';
export { initialChatState } from '../chat.js';

export function reduceKimiCodeEvent(state: ChatState, ev: any, seq: number): ChatState {
  return reduceAcpEvent(state, ev, seq);
}
