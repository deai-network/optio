// Grok binding of the engine-neutral ACP reducer (src/acp/events.ts) — grok
// speaks stock ACP (JSON-RPC 2.0), so the binding is a pure re-export. Wire
// shapes are pinned in optio-grok's conversation.py; the shared reducer's
// behavior is pinned by grok-events.test.ts.
import type { ChatState } from '../chat.js';
import { reduceAcpEvent } from '../acp/events.js';
export { initialChatState } from '../chat.js';

export function reduceGrokEvent(state: ChatState, ev: any, seq: number): ChatState {
  return reduceAcpEvent(state, ev, seq);
}
