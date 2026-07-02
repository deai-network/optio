// Cursor binding of the engine-neutral ACP reducer (src/acp/events.ts) —
// cursor-agent's `acp` subcommand speaks the same public ACP (JSON-RPC 2.0)
// grok does, so the binding is a pure re-export. Wire shapes are grok-pinned
// (optio-grok's conversation.py); cursor runtime divergences, if any surface,
// get handled here without touching the shared reducer.
import type { ChatState } from '../chat.js';
import { reduceAcpEvent } from '../acp/events.js';
export { initialChatState } from '../chat.js';

export function reduceCursorEvent(state: ChatState, ev: any, seq: number): ChatState {
  return reduceAcpEvent(state, ev, seq);
}
