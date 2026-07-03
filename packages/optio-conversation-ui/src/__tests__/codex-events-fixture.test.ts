import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { initialChatState, type ChatState } from '../chat.js';
import { reduceCodexEvent } from '../codex/events.js';

// Layer 3 (guide "the real wire against the real reducer"): replay a REAL
// codex app-server event stream — captured once by the opt-in Python harness
// test_real_codex_conversation.py — through the real reducer, asserting the
// resulting ChatState is what a human should see. Fakes emit idealized events;
// only the real wire (interleaved reasoning + answer deltas from a reasoning
// model) exposes the reducer's real coalescing failure modes.
//
// The fixture is committed only after a real capture (the opt-in harness writes
// it); until then it is absent and this suite skips cleanly. Resolve it from
// the package root (vitest runs with cwd = package dir) rather than
// import.meta.url — vite inlines top-level import.meta.url as a non-file URL
// under jsdom, so fileURLToPath throws at module-eval time.
const fixturePath = resolve(process.cwd(), 'src/__tests__/fixtures/codex-events.json');
const present = existsSync(fixturePath);

function play(events: any[]): ChatState {
  return events.reduce((s, ev, i) => reduceCodexEvent(s, ev, i), initialChatState);
}

describe.skipIf(!present)('codex reducer — recorded real wire (Layer 3)', () => {
  const events: any[] = present ? JSON.parse(readFileSync(fixturePath, 'utf8')) : [];

  it('a real reasoning-model turn coalesces into ONE answer bubble', () => {
    const st = play(events);
    const assistants = st.items.filter((i) => i.kind === 'assistant');
    // The bug this guards: a reasoning model interleaves thought-deltas with
    // answer-deltas; a tail-position reducer fragments the answer into a bubble
    // per token. The real reducer coalesces by turn/message id.
    expect(assistants.length).toBe(1);
    expect((assistants[0] as any).text.length).toBeGreaterThan(0);
  });

  it('reasoning renders as its own activity row IFF the turn produced a summary', () => {
    const st = play(events);
    // A trivial turn (e.g. "reply PONG") emits a reasoning item with
    // summary:[]/content:[] and NO summaryTextDelta — real, observed on the
    // captured wire. The reducer correctly emits no activity row then; only a
    // turn that actually carried reasoning summary text should render one.
    // Assert against what THIS capture contains rather than over-asserting.
    const hasReasoningText = events.some(
      (e: any) =>
        e.method === 'item/reasoning/summaryTextDelta' ||
        e.method === 'item/reasoning/textDelta' ||
        (e.params?.item?.type === 'reasoning' &&
          ((e.params.item.summary?.length ?? 0) > 0 ||
            (e.params.item.content?.length ?? 0) > 0)),
    );
    expect(st.items.some((i) => i.kind === 'activity')).toBe(hasReasoningText);
  });

  it('busy is cleared at turn end', () => {
    const st = play(events);
    expect(st.busy).toBe(false);
  });
});
