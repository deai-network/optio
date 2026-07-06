// Layer-3 (design §5, plan Task S3) DEFERRED opt-in: replay a REAL captured
// antigravity transcript through the production reducer.
//
// The antigravity-events.test.ts suite drives the reducer with HAND-WRITTEN
// transcript shapes (fake_agy.py's documented minimal schema). This file
// replays a transcript.jsonl captured from a real `agy -p` turn (real tool
// calls, any reasoning rows, the coalesced final answer) through the exact
// `reduceAntigravityEvent` the listener feeds over SSE — proving the reducer
// yields ONE coalesced, finalized answer bubble and clears `busy` at turn end
// on the real wire, not just the fake one.
//
// The fixture is captured by the S3 spike (plan Task S3) — it needs a real
// Google-authed `agy`, which this environment lacks — so it is NOT committed
// yet. Until it exists this test SKIPS cleanly (it.skipIf), so the harness never
// fakes a pass on a capture it does not have. To produce it: run one real turn
// under a PTY (`script -qec 'agy -p --dangerously-skip-permissions "read README
// and reply DONE"' /dev/null`) and copy each JSON line of
// ~/.gemini/antigravity/transcript.jsonl into the JSON array at the path below.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatItem, type ChatState } from '../chat.js';
import { reduceAntigravityEvent } from '../antigravity/events.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(HERE, 'fixtures', 'antigravity-transcript.json');
const HAVE_FIXTURE = fs.existsSync(FIXTURE);

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceAntigravityEvent(s, ev, i), from);
}

describe('antigravity real transcript → reducer (capture-replay)', () => {
  it.skipIf(!HAVE_FIXTURE)(
    'coalesces a real turn into one finalized answer bubble, tool rows preserved, busy cleared',
    () => {
      const events: any[] = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'));
      expect(events.length).toBeGreaterThan(0);

      const state = play(events);

      // Exactly one coalesced, finalized assistant answer bubble for the turn.
      const answers = state.items.filter(
        (i): i is Extract<ChatItem, { kind: 'assistant' }> => i.kind === 'assistant',
      );
      expect(answers).toHaveLength(1);
      expect(answers[0].text.length).toBeGreaterThan(0);
      expect(answers[0].pending).toBe(false);

      // The turn ended (the assistant line is the turn end) → not busy.
      expect(state.busy).toBe(false);

      // If the real turn made tool calls, they survive as durable transcript
      // rows (history), not dropped by the answer bubble.
      const hadTool = events.some((e) => e?.type === 'tool');
      if (hadTool) {
        expect(state.items.some((i) => i.kind === 'tool')).toBe(true);
      }
    },
  );
});
