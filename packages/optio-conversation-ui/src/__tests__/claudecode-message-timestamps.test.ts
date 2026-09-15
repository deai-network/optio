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
  // initiating user prompt. `endTimestamp` is the LAST assistant wire event
  // seen for the message, full stop (review of fix 12, finding 2): the
  // message's second assistant event, line 161, is a tool_use block -- it
  // carries no text, but it is still the LAST assistant event of this
  // message, so it is what `endTimestamp` ends up holding, not the earlier
  // text block's own time.
  it('without a marker, the assistant bubble\'s start falls back to the previous wire event; end is the message\'s LAST assistant event (a trailing tool_use), not its last text block', () => {
    const s = replay(events);
    const bubble = ofKind(s, 'assistant')[0];
    expect(bubble.timestamp).toBe(Date.parse('2026-09-14T04:57:00.765Z'));
    expect(bubble.endTimestamp).toBe(Date.parse('2026-09-14T04:57:09.612Z'));
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
      expect(bubble.endTimestamp).toBe(Date.parse('2026-09-14T04:57:09.612Z'));
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

describe('claudecode real wire: message timestamps (narration-tools fixture, hidden thinking)', () => {
  // Review of fix 12, finding 1: claudecode-narration-tools.jsonl's fourth
  // message (msg_011Cexri1QWoKypvm9uzsBoj) opens with a HIDDEN thinking block
  // (thinking: "", blockText null -- the real reasoning, not narration)
  // before its text -- exactly the pattern that lost the marker: the old
  // code cleared `pendingMessageStart` the instant ANY assistant event for
  // this message id arrived, regardless of whether that event's blocks
  // opened a bubble, so the hidden thinking event threw the marker away and
  // the later text block fell back to the previous wire event (here, that
  // fallback happens to equal the hidden thinking block's own late
  // completing time, 03:45:33.525 -- 17s after the marker).
  const events = load('claudecode-narration-tools.jsonl');
  const TARGET_MSG_ID = 'msg_011Cexri1QWoKypvm9uzsBoj';

  it('a marker inserted before a message that opens with hidden thinking gives an exact start, live and replay alike', () => {
    const idx = events.findIndex(
      (e) => e.type === 'stream_event' && e.event?.type === 'message_start' && e.event.message.id === TARGET_MSG_ID,
    );
    expect(idx).toBeGreaterThan(-1);
    // Between the marker and the true start: the fixture's own true start
    // (the user turn that triggers this message) is 03:45:16.808Z; the
    // hidden thinking block doesn't complete until 03:45:33.525Z. Any value
    // in that window proves the marker, not either fallback, won.
    const markerTs = Date.parse('2026-09-12T03:45:20.000Z');
    const withMarker = [
      ...events.slice(0, idx),
      { type: 'x-optio-message-start', id: TARGET_MSG_ID, ts: markerTs },
      ...events.slice(idx),
    ];
    for (const s of [live(withMarker), replay(withMarker)]) {
      const bubble = ofKind(s, 'assistant').find((a) => a.msgId === TARGET_MSG_ID);
      expect(bubble).toBeDefined();
      expect(bubble?.timestamp).toBe(markerTs);
      expect(bubble?.endTimestamp).toBe(Date.parse('2026-09-12T03:45:33.674Z'));
    }
  });
});

describe('claudecode real wire: message timestamps (two-steers fixture)', () => {
  // Fix 12: the same start/end split, cross-checked against a second real
  // recording (two steers taken across one streamed answer -- two assistant
  // messages, each a single text block, no trailing tool_use).
  const events = load('claudecode-two-steers-streaming.jsonl');

  it('live and replay give the exact same start AND end for every assistant bubble', () => {
    const l = live(events);
    const r = replay(events);
    expect(ofKind(l, 'assistant').map((a) => a.timestamp)).toEqual(ofKind(r, 'assistant').map((a) => a.timestamp));
    expect(ofKind(l, 'assistant').map((a) => a.endTimestamp)).toEqual(ofKind(r, 'assistant').map((a) => a.endTimestamp));
  });

  // Review of fix 12, finding 3: exact fallback values, derived from the
  // wire, replacing the earlier toBeDefined-only check. Both messages have
  // no marker in this fixture as recorded: message 1's bubble falls back to
  // the previous wire event, the initiating user prompt (04:57:05.968Z);
  // message 2's bubble falls back to the previous wire event AT THE TIME ITS
  // OWN bubble opens -- the steer's taking echo (04:57:26.024Z), not the
  // earlier message. Each message has exactly one (text) assistant event on
  // the wire, so `endTimestamp` equals that same event's own time.
  it('without a marker, each assistant bubble resolves the exact previous-wire-event fallback for both start and end', () => {
    const s = replay(events);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles).toHaveLength(2);
    expect(bubbles[0].timestamp).toBe(Date.parse('2026-09-14T04:57:05.968Z'));
    expect(bubbles[0].endTimestamp).toBe(Date.parse('2026-09-14T04:57:25.828Z'));
    expect(bubbles[1].timestamp).toBe(Date.parse('2026-09-14T04:57:26.024Z'));
    expect(bubbles[1].endTimestamp).toBe(Date.parse('2026-09-14T04:57:29.962Z'));
  });

  // Review of fix 12, finding 3: the brief requires this fixture checked
  // WITH a marker too -- one inserted before EACH message_start, not just
  // the first -- asserting start = marker ts and end = the message's last
  // block, live and on replay, for BOTH bubbles.
  it('with a marker inserted before EACH message_start, both bubbles get their marker as start; end is unaffected', () => {
    const starts = events
      .map((e, i) => ({ e, i }))
      .filter(({ e }) => e.type === 'stream_event' && e.event?.type === 'message_start');
    expect(starts).toHaveLength(2);
    const markerTs1 = Date.parse('2026-09-14T04:57:10.000Z');
    const markerTs2 = Date.parse('2026-09-14T04:57:27.000Z');
    const [first, second] = starts;
    const withMarkers = [
      ...events.slice(0, first.i),
      { type: 'x-optio-message-start', id: first.e.event.message.id, ts: markerTs1 },
      ...events.slice(first.i, second.i),
      { type: 'x-optio-message-start', id: second.e.event.message.id, ts: markerTs2 },
      ...events.slice(second.i),
    ];
    for (const s of [live(withMarkers), replay(withMarkers)]) {
      const bubbles = ofKind(s, 'assistant');
      expect(bubbles).toHaveLength(2);
      expect(bubbles[0].timestamp).toBe(markerTs1);
      expect(bubbles[0].endTimestamp).toBe(Date.parse('2026-09-14T04:57:25.828Z'));
      expect(bubbles[1].timestamp).toBe(markerTs2);
      expect(bubbles[1].endTimestamp).toBe(Date.parse('2026-09-14T04:57:29.962Z'));
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

  // Review of fix 12, finding 2: `endTimestamp` is the LAST assistant wire
  // event of the message, full stop -- including a trailing tool_use (no
  // text at all) or a hidden thinking block (empty `thinking`, blockText
  // null), neither of which goes through applyBlockText/applyInterruptedText.
  it('a trailing tool_use event (no text) still moves endTimestamp to its own time, since it is the LAST assistant event of the message', () => {
    const t0 = '2026-09-13T12:00:00.000Z';
    const t1 = '2026-09-13T12:00:01.000Z';
    const t2 = '2026-09-13T12:00:02.000Z';
    const toolUse = (msgId: string, timestamp: string) => ({
      type: 'assistant',
      timestamp,
      message: { role: 'assistant', id: msgId, content: [{ type: 'tool_use', id: 'call1', name: 'Bash', input: {} }] },
    });
    const s = run([
      user('q', t0),
      assistantText('Hello', 'm1', t1),
      toolUse('m1', t2),
    ]);
    const bubble = s.items.find((i) => i.kind === 'assistant');
    expect(bubble?.timestamp).toBe(Date.parse(t0));
    expect(bubble?.endTimestamp).toBe(Date.parse(t2));
  });

  it('a trailing hidden-thinking event (empty thinking, no rendered text) still moves endTimestamp to its own time', () => {
    const t0 = '2026-09-13T12:00:00.000Z';
    const t1 = '2026-09-13T12:00:01.000Z';
    const t2 = '2026-09-13T12:00:02.000Z';
    const hiddenThinking = (msgId: string, timestamp: string) => ({
      type: 'assistant',
      timestamp,
      message: { role: 'assistant', id: msgId, content: [{ type: 'thinking', thinking: '' }] },
    });
    const s = run([
      user('q', t0),
      assistantText('Hello', 'm1', t1),
      hiddenThinking('m1', t2),
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

    // Review of fix 12, finding 1: a marker must survive an assistant event
    // for its own message id that opens no bubble at all -- a hidden
    // thinking block (empty `thinking`, blockText null) or a tool_use-first
    // event, both real-recording patterns (claudecode-narration-tools.jsonl,
    // claudecode-steer-then-interrupt.jsonl). Before the fix, the marker was
    // cleared unconditionally the moment ANY assistant event for its message
    // id arrived, so it was thrown away here and the later text block fell
    // back to the previous wire event -- effectively this same hidden
    // thinking event's own (late) completing time.
    const hiddenThinking = (msgId: string, timestamp: string) => ({
      type: 'assistant',
      timestamp,
      message: { role: 'assistant', id: msgId, content: [{ type: 'thinking', thinking: '' }] },
    });
    const toolUse = (msgId: string, timestamp: string) => ({
      type: 'assistant',
      timestamp,
      message: { role: 'assistant', id: msgId, content: [{ type: 'tool_use', id: 'call1', name: 'Bash', input: {} }] },
    });

    it('a marker survives a hidden-thinking-only assistant event for its own message (opens no bubble) and is applied once the message\'s text finally arrives', () => {
      const markerTs = Date.parse('2026-09-13T12:00:00.500Z');
      const s = run([
        user('q', '2026-09-13T11:59:59.000Z'),
        messageStart('m1', markerTs),
        hiddenThinking('m1', '2026-09-13T12:00:20.000Z'),
        assistantText('answer', 'm1', '2026-09-13T12:00:21.000Z'),
      ]);
      const bubble = s.items.find((i) => i.kind === 'assistant');
      expect(bubble?.timestamp).toBe(markerTs);
      expect(bubble?.endTimestamp).toBe(Date.parse('2026-09-13T12:00:21.000Z'));
    });

    it('a marker survives a tool_use-only assistant event for its own message (opens no bubble) and is applied once the message\'s text finally arrives', () => {
      const markerTs = Date.parse('2026-09-13T12:00:00.500Z');
      const s = run([
        user('q', '2026-09-13T11:59:59.000Z'),
        messageStart('m1', markerTs),
        toolUse('m1', '2026-09-13T12:00:20.000Z'),
        assistantText('answer', 'm1', '2026-09-13T12:00:21.000Z'),
      ]);
      const bubble = s.items.find((i) => i.kind === 'assistant');
      expect(bubble?.timestamp).toBe(markerTs);
      expect(bubble?.endTimestamp).toBe(Date.parse('2026-09-13T12:00:21.000Z'));
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

  // Fix 14 (owner ruling 2026-09-15, manual-test finding): the view now gives
  // ONLY a "System: " row the user bubble's corner treatment and a
  // right-aligned time label -- but a background-task/upload-notice row
  // shares this same (non-muted) branch and, like a muted notice, never
  // carries a timestamp either, so `timestamp` presence alone can't tell
  // them apart. The reducer flags a "System: " echo `system: true` so the
  // view has a real discriminator instead of sniffing text.
  it('a "System: " echo is flagged system: true', () => {
    const t = '2026-09-14T14:33:01.316Z';
    const s = run([user('System: you have been resumed', t)]);
    expect(ofKind(s, 'activity')[0]).toMatchObject({ system: true });
  });

  it('the operator-interrupt notice row (muted) still carries no timestamp', () => {
    const s = run([user('q', '2026-09-14T14:00:00.000Z'), { type: 'x-optio-interrupt' }]);
    const notice = ofKind(s, 'activity').find((i) => i.muted);
    expect(notice).toBeDefined();
    expect(notice?.timestamp).toBeUndefined();
    expect(notice?.system).toBeUndefined();
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
