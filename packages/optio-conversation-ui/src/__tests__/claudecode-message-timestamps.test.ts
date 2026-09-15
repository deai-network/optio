// Message timestamps (Fix 4, owner ruling 2026-09-14: "all messages need a
// timestamp"). Real Claude Code stream-json (see
// claudecode-steering-real-wire.test.ts for the fixture's full shape):
// claudecode-steer-streaming.jsonl carries a real user prompt, a real
// assistant answer, and a steer whose queued-echo wire timestamp
// (2026-09-14T04:57:02.996Z) is EARLIER than the tool_result that follows it
// on the wire (2026-09-14T04:57:09.754Z) -- the send time, not the take time.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
function load(name: string): any[] {
  return fs
    .readFileSync(path.join(HERE, 'fixtures', name), 'utf-8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line));
}

// A reload hours after the run: the reducer clock is far from the wire times,
// so any timestamp that matched would prove the reducer read its own clock.
const NOW = Date.parse('2026-09-14T09:00:00.000Z');

function live(events: any[]): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, NOW), initialChatState);
}
function replay(events: any[]): ChatState {
  return events.reduce(
    (s, ev, i) => (ev.type === 'stream_event' ? s : reduceEvent(s, ev, i + 1, NOW)),
    initialChatState,
  );
}
function ofKind<K extends ChatItem['kind']>(state: ChatState, kind: K): Extract<ChatItem, { kind: K }>[] {
  return state.items.filter((i) => i.kind === kind) as Extract<ChatItem, { kind: K }>[];
}

describe('claudecode real wire: message timestamps', () => {
  const events = load('claudecode-steer-streaming.jsonl');

  it('the user prompt carries its own wire send time', () => {
    const s = replay(events);
    expect(ofKind(s, 'user')[0].timestamp).toBe(Date.parse('2026-09-14T04:57:00.765Z'));
  });

  // Fix 12 (owner ruling 2026-09-14): `timestamp` is now the message's START,
  // not "the first wire ts seen" (that was Fix 4's bug -- see fix-12-brief.md
  // Facts: for a plain-text answer that first-seen ts is the END of the first
  // block, 6.5-15.4s after the true start). This fixture predates the marker
  // this fix adds, so the fallback applies: the previous wire event
  // (lastEventAt as it stood just before the bubble opened) -- here the
  // initiating user prompt. `endTimestamp` is the LAST TEXT/thinking block's
  // own wire time (the message's second assistant event, line160, is a
  // tool_use block -- it does not extend the bubble's text, so it does not
  // move endTimestamp either).
  it('without a marker, the assistant bubble\'s start falls back to the previous wire event; end is its last text block\'s own time', () => {
    const s = replay(events);
    const bubble = ofKind(s, 'assistant')[0];
    expect(bubble.timestamp).toBe(Date.parse('2026-09-14T04:57:00.765Z'));
    expect(bubble.endTimestamp).toBe(Date.parse('2026-09-14T04:57:09.321Z'));
  });

  it('the second message (after the steer) falls back to the LATEST wire event seen so far -- not the earlier-timestamped steer echo that preceded it on the wire', () => {
    const s = replay(events);
    const bubble = ofKind(s, 'assistant')[1];
    expect(bubble.timestamp).toBe(Date.parse('2026-09-14T04:57:09.754Z'));
    expect(bubble.endTimestamp).toBe(Date.parse('2026-09-14T04:57:11.805Z'));
  });

  it('Fix 12: an x-optio-message-start marker inserted before the real message_start gives an exact start, live and replay alike', () => {
    const idx = events.findIndex((e) => e.type === 'stream_event' && e.event?.type === 'message_start');
    const msgId = events[idx].event.message.id;
    const markerTs = Date.parse('2026-09-14T04:57:05.000Z');
    const withMarker = [
      ...events.slice(0, idx),
      { type: 'x-optio-message-start', id: msgId, ts: markerTs },
      ...events.slice(idx),
    ];
    for (const s of [live(withMarker), replay(withMarker)]) {
      const bubble = ofKind(s, 'assistant')[0];
      expect(bubble.timestamp).toBe(markerTs);
      expect(bubble.endTimestamp).toBe(Date.parse('2026-09-14T04:57:09.321Z'));
    }
  });

  it('a taken steer shows the ECHO\'s wire send time, not the (later) take moment', () => {
    const s = replay(events);
    const steer = ofKind(s, 'user')[1];
    expect(steer).toMatchObject({ queueId: 'q1' });
    expect(steer.timestamp).toBe(Date.parse('2026-09-14T04:57:02.996Z'));
  });

  it('live and replay give the exact same timestamps (start AND end)', () => {
    const l = live(events);
    const r = replay(events);
    expect(ofKind(l, 'user').map((u) => u.timestamp)).toEqual(ofKind(r, 'user').map((u) => u.timestamp));
    expect(ofKind(l, 'assistant').map((a) => a.timestamp)).toEqual(ofKind(r, 'assistant').map((a) => a.timestamp));
    expect(ofKind(l, 'assistant').map((a) => a.endTimestamp)).toEqual(ofKind(r, 'assistant').map((a) => a.endTimestamp));
  });

  it('a still-queued bubble with no echo yet carries no timestamp -- never invented', () => {
    const cut = events.findIndex((e) => e.type === 'x-optio-queued');
    const s = live(events.slice(0, cut + 1));
    const q = ofKind(s, 'user').find((u) => u.queued);
    expect(q).toBeDefined();
    expect(q?.timestamp).toBeUndefined();
  });
});

describe('claudecode real wire: message timestamps (two-steers fixture)', () => {
  // Fix 12: the same start/end split, cross-checked against a second real
  // recording (two steers taken across one streamed answer).
  const events = load('claudecode-two-steers-streaming.jsonl');

  it('live and replay give the exact same start AND end for every assistant bubble', () => {
    const l = live(events);
    const r = replay(events);
    expect(ofKind(l, 'assistant').map((a) => a.timestamp)).toEqual(ofKind(r, 'assistant').map((a) => a.timestamp));
    expect(ofKind(l, 'assistant').map((a) => a.endTimestamp)).toEqual(ofKind(r, 'assistant').map((a) => a.endTimestamp));
  });

  it('without a marker, every assistant bubble still resolves a start (the fallback chain never leaves a mid-conversation message timeless)', () => {
    const s = replay(events);
    for (const bubble of ofKind(s, 'assistant')) {
      expect(bubble.timestamp).toBeDefined();
      expect(bubble.endTimestamp).toBeDefined();
    }
  });
});

describe('claudecode message timestamps: synthetic events', () => {
  const user = (text: string, timestamp?: string) => ({
    type: 'user',
    timestamp,
    message: { role: 'user', content: [{ type: 'text', text }] },
  });
  const assistantText = (text: string, msgId?: string, timestamp?: string) => ({
    type: 'assistant',
    timestamp,
    message: { role: 'assistant', id: msgId, content: [{ type: 'text', text }] },
  });
  const delta = (text: string) => ({
    type: 'stream_event',
    event: { type: 'content_block_delta', delta: { type: 'text_delta', text } },
  });
  const localUser = (text: string, id: string | undefined, time: number, queued = false) => ({
    type: 'x-optio-local-user',
    text,
    id,
    queued,
    time,
  });
  const result = (text: string) => ({ type: 'result', subtype: 'success', result: text });
  const NOW2 = Date.parse('2026-09-13T17:49:00.000Z');
  function run(events: any[]): ChatState {
    return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, NOW2), initialChatState);
  }

  it('an idle local echo carries the view-supplied send time, and the wire echo replaces it with its own', () => {
    const sentAt = Date.parse('2026-09-13T12:00:00.000Z');
    const wireAt = Date.parse('2026-09-13T12:00:00.400Z');
    const mid = run([localUser('hi', 's1', sentAt)]);
    expect(mid.items[0]).toMatchObject({ kind: 'user', timestamp: sentAt });
    const s = run([localUser('hi', 's1', sentAt), user('hi', new Date(wireAt).toISOString())]);
    expect(s.items[0]).toMatchObject({ kind: 'user', timestamp: wireAt });
  });

  // Fix 12: a text-opening bubble resolves its START the moment it opens (the
  // fallback -- the previous wire event, here the user prompt -- is already
  // known synchronously), but its END stays unresolved until an actual
  // assistant event finalizes a block; a delta carries no timestamp to give it
  // one.
  it('a streaming delta with no wire event yet resolves a start (fallback: the previous wire event) but no end yet', () => {
    const s = run([user('q', '2026-09-13T12:00:00.000Z'), delta('Hel')]);
    const bubble = s.items.find((i) => i.kind === 'assistant');
    expect(bubble).toMatchObject({ timestamp: Date.parse('2026-09-13T12:00:00.000Z') });
    expect(bubble?.endTimestamp).toBeUndefined();
  });

  it('the assistant bubble\'s endTimestamp tracks the LAST wire event for the message; its start (resolved once, at creation) is never overwritten', () => {
    const t0 = '2026-09-13T12:00:00.000Z'; // the initiating prompt -- this message's start fallback
    const t1 = '2026-09-13T12:00:01.000Z';
    const t2 = '2026-09-13T12:00:02.000Z';
    const s = run([
      user('q', t0),
      delta('Hel'),
      assistantText('Hello', 'm1', t1),
      // A second event for the SAME message id updates endTimestamp (LAST
      // wins) but must never touch the already-resolved start.
      { ...assistantText('Hello there', 'm1', t2) },
    ]);
    const bubble = s.items.find((i) => i.kind === 'assistant');
    expect(bubble?.timestamp).toBe(Date.parse(t0));
    expect(bubble?.endTimestamp).toBe(Date.parse(t2));
  });

  // Fix 12 fallback chain, in priority order: x-optio-message-start marker >
  // the message's own first (thinking) block > the previous wire event > none.
  describe('Fix 12: start/end fallback chain', () => {
    const messageStart = (id: string, ts: number) => ({ type: 'x-optio-message-start', id, ts });
    const thinkingText = (text: string, msgId: string, timestamp: string) => ({
      type: 'assistant',
      timestamp,
      message: { role: 'assistant', id: msgId, content: [{ type: 'thinking', thinking: text }] },
    });

    it('a marker resolves the exact start, overriding the previous-wire-event fallback', () => {
      const s = run([
        user('q', '2026-09-13T11:59:59.000Z'),
        messageStart('m1', Date.parse('2026-09-13T12:00:00.500Z')),
        assistantText('answer', 'm1', '2026-09-13T12:00:05.000Z'),
      ]);
      const bubble = s.items.find((i) => i.kind === 'assistant');
      expect(bubble?.timestamp).toBe(Date.parse('2026-09-13T12:00:00.500Z'));
      expect(bubble?.endTimestamp).toBe(Date.parse('2026-09-13T12:00:05.000Z'));
    });

    it('without a marker, a message opening with a thinking (narration) block uses that block\'s own completing time as its start', () => {
      const s = run([
        user('q', '2026-09-13T11:59:59.000Z'),
        thinkingText('let me think', 'm1', '2026-09-13T12:00:03.000Z'),
        assistantText('answer', 'm1', '2026-09-13T12:00:05.000Z'),
      ]);
      const bubble = s.items.find((i) => i.kind === 'assistant');
      expect(bubble?.timestamp).toBe(Date.parse('2026-09-13T12:00:03.000Z'));
      expect(bubble?.endTimestamp).toBe(Date.parse('2026-09-13T12:00:05.000Z'));
    });

    it('with no marker and no prior wire event (the very first message of the conversation), the bubble gets no start at all -- never invented', () => {
      const s = run([assistantText('answer', 'm1', '2026-09-13T12:00:05.000Z')]);
      const bubble = s.items.find((i) => i.kind === 'assistant');
      expect(bubble?.timestamp).toBeUndefined();
      expect(bubble?.endTimestamp).toBe(Date.parse('2026-09-13T12:00:05.000Z'));
    });

    it('a marker recorded for a different message id is left pending, not consumed by an unrelated message', () => {
      const s = run([
        messageStart('other-id', 999),
        assistantText('answer', 'm1', '2026-09-13T12:00:05.000Z'),
      ]);
      const bubble = s.items.find((i) => i.kind === 'assistant');
      expect(bubble?.timestamp).toBeUndefined();
    });
  });

  it('an is_error result gets a timestamp derived from the wire, not an invented one', () => {
    const s = run([
      user('q', '2026-09-13T12:00:00.000Z'),
      { type: 'result', subtype: 'error_during_execution', is_error: true, result: 'boom', api_error_status: 500 },
    ]);
    const err = s.items.find((i) => i.kind === 'error');
    expect(err?.timestamp).toBe(Date.parse('2026-09-13T12:00:00.000Z'));
  });

  it('a normal (non-error) result never adds a timestamp to a fresh error item -- sanity', () => {
    const s = run([user('q', '2026-09-13T12:00:00.000Z'), assistantText('ok', 'm1', '2026-09-13T12:00:01.000Z'), result('ok')]);
    expect(s.items.some((i) => i.kind === 'error')).toBe(false);
  });

  it('an is_error result with no prior timestamped user/assistant event gives an error item with no timestamp -- never invented', () => {
    const s = run([{ type: 'result', subtype: 'error_during_execution', is_error: true, result: 'boom', api_error_status: 500 }]);
    const err = s.items.find((i) => i.kind === 'error');
    expect(err).toBeDefined();
    expect(err?.timestamp).toBeUndefined();
  });

  // Fix 4 review round 1, finding 1: a busy send races x-optio-queued (the
  // listener broadcasts it before /send returns) ahead of the local echo the
  // view dispatches only after `await postJson('send')`. The queued bubble
  // x-optio-queued creates carries no time; the local echo that follows must
  // still backfill it instead of hitting the "got here first" branch and
  // discarding ev.time. The taking wire echo (isReplay in real traffic; here
  // any wire `user` event with matching text) then wins over that.
  //
  // Review of fix 8, finding 1: x-optio-queued only pins a genuinely-busy
  // "Send when ready" bubble while the agent reads as busy (an
  // interrupt_and_send's OWN x-optio-queued fires once the turn it
  // interrupted has already ended, and must not pin one -- see the reducer
  // tests in claudecode-steering.test.ts). A leading in-flight turn is what
  // makes the agent busy here, same as a real "Send when ready" would race.
  it('x-optio-queued(q1) then x-optio-local-user(q1, time T) backfills T on the still-queued bubble; the taking echo then wins', () => {
    const sentAt = Date.parse('2026-09-13T12:00:00.000Z');
    const wireAt = Date.parse('2026-09-13T12:00:01.500Z');
    const busyTurn = user('q', '2026-09-13T11:59:59.000Z');
    const queued = { type: 'x-optio-queued', id: 'q1', text: 'later' };
    const afterQueued = run([busyTurn, queued]);
    expect(ofKind(afterQueued, 'user')[1]).toMatchObject({ queued: true, queueId: 'q1' });
    expect(ofKind(afterQueued, 'user')[1].timestamp).toBeUndefined();

    const afterLocal = run([busyTurn, queued, localUser('later', 'q1', sentAt, true)]);
    expect(ofKind(afterLocal, 'user')[1]).toMatchObject({ queued: true, queueId: 'q1', timestamp: sentAt });

    const taken = run([busyTurn, queued, localUser('later', 'q1', sentAt, true), user('later', new Date(wireAt).toISOString())]);
    const takenItem = ofKind(taken, 'user')[1];
    expect(takenItem).toMatchObject({ queueId: 'q1', timestamp: wireAt });
    expect(takenItem.queued).toBeUndefined();
  });
});

// Fix 9 (owner ruling 2026-09-14, manual-test finding 3): "System: …" harness
// echoes (e.g. "System: deliverable … accepted." and "System: you have been
// resumed", session.py:818-820 and :687) become activity rows, and the row
// must show the echo's own wire time — never the reducer's clock, and never
// invented when the wire carries none. Muted notice rows (operator interrupt,
// undelivered message) stay timeless.
describe('claudecode message timestamps: "System: " activity rows (Fix 9)', () => {
  const user = (text: string, timestamp?: string) => ({
    type: 'user',
    timestamp,
    message: { role: 'user', content: [{ type: 'text', text }] },
  });
  const NOW3 = Date.parse('2026-09-14T17:00:00.000Z');
  function run(events: any[]): ChatState {
    return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, NOW3), initialChatState);
  }

  it('a "System: you have been resumed" echo carries the echo\'s wire timestamp', () => {
    const t = '2026-09-14T14:33:01.316Z';
    const s = run([user('System: you have been resumed', t)]);
    expect(ofKind(s, 'activity')[0]).toMatchObject({ timestamp: Date.parse(t) });
  });

  it('a "System: deliverable … accepted" echo carries the echo\'s wire timestamp', () => {
    const t = '2026-09-14T14:33:05.000Z';
    const s = run([user('System: deliverable mission-report.txt: accepted. thanks for the good work.', t)]);
    expect(ofKind(s, 'activity')[0]).toMatchObject({ timestamp: Date.parse(t) });
  });

  it('a "System: " echo with no wire timestamp gives an activity row with none -- never invented from the reducer clock', () => {
    const s = run([user('System: you have been resumed')]);
    expect(ofKind(s, 'activity')[0].timestamp).toBeUndefined();
  });

  it('the operator-interrupt notice row (muted) still carries no timestamp', () => {
    const s = run([user('q', '2026-09-14T14:00:00.000Z'), { type: 'x-optio-interrupt' }]);
    const notice = ofKind(s, 'activity').find((i) => i.muted);
    expect(notice).toBeDefined();
    expect(notice?.timestamp).toBeUndefined();
  });

  it('an undelivered-message notice row still carries no timestamp', () => {
    // Review of fix 8, finding 1: x-optio-queued only pins a queued bubble
    // while the agent reads as busy; a leading in-flight turn establishes that
    // (see claudecode-steering.test.ts for the reducer-level coverage).
    const s = run([
      user('q'),
      { type: 'x-optio-queued', id: 'q1', text: 'later' },
      { type: 'x-optio-closed', reason: 'done' },
    ]);
    const notice = ofKind(s, 'activity').find((i) => i.text.startsWith('Not delivered'));
    expect(notice).toBeDefined();
    expect(notice?.timestamp).toBeUndefined();
  });
});
