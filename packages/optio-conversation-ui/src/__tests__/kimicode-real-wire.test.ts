// Layer-3 (design §5) DEFERRED opt-in: replay a REAL captured kimi ACP turn
// through the production reducer.
//
// The kimicode-events.test.ts suite drives the reducer with HAND-WRITTEN ACP
// shapes. This file replays a turn captured off the WIRE from a real
// `kimi acp` session (interleaved agent_thought_chunk + agent_message_chunk,
// real tool_call / tool_call_update, the session/prompt turn-end carrying
// stopReason) through the exact `reduceKimiCodeEvent` the listener feeds over
// SSE — proving the reducer coalesces a real reasoning model's interleaved
// stream into ONE answer bubble, renders reasoning as distinct thinking rows,
// and clears `busy` at turn-end.
//
// The fixture is captured from a live kimi and is NOT committed yet (needs a
// real authed binary — the row-30 real-binary follow-up, tracked in
// docs/2026-07-03-optio-kimicode-parity.md). Until it exists this test SKIPS
// cleanly (it.skipIf), so the harness never fakes a pass on a capture it does
// not have. To produce it: capture the raw JSON-RPC objects kimi emits for one
// turn (see optio-kimicode conversation.py `run_reader`) into the path below.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatItem, type ChatState } from '../chat.js';
import { reduceKimiCodeEvent } from '../kimicode/events.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(HERE, 'fixtures', 'kimicode-acp-turn.json');
const HAVE_FIXTURE = fs.existsSync(FIXTURE);

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceKimiCodeEvent(s, ev, i), from);
}

describe('kimicode real ACP wire → reducer (capture-replay)', () => {
  it.skipIf(!HAVE_FIXTURE)(
    'coalesces a real interleaved turn into one answer + reasoning rows, busy cleared',
    () => {
      const events: any[] = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'));
      expect(events.length).toBeGreaterThan(0);

      const state = play(events);

      // Exactly one coalesced, finalized assistant answer bubble.
      const answers = state.items.filter(
        (i): i is Extract<ChatItem, { kind: 'assistant' }> => i.kind === 'assistant',
      );
      expect(answers).toHaveLength(1);
      expect(answers[0].text.length).toBeGreaterThan(0);
      expect(answers[0].pending).toBe(false);

      // The turn ended → the agent is no longer busy.
      expect(state.busy).toBe(false);

      // If the real capture interleaved reasoning, it renders as distinct
      // thinking rows (never folded into the answer).
      const hadThought = events.some(
        (e) => e?.params?.update?.sessionUpdate === 'agent_thought_chunk',
      );
      if (hadThought) {
        expect(state.items.some((i) => i.kind === 'thinking')).toBe(true);
      }
    },
  );
});
