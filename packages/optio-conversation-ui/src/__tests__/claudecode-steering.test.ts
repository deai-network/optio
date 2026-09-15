import { describe, expect, it } from 'vitest';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';
import { bundleUploadNotice } from '../uploads.js';

// Steering events through the claudecode reducer. Wire builders as in
// claudecode-events.test.ts; x-optio-* are the listener's synthetic events
// and the view's local echo.
const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
// Fix 13b: a wire echo carrying the CLI's own uuid (isReplay, per
// cli-queue-lifecycle.md); `opts` adds `isReplay`/`timestamp`/extra text
// blocks (a fold's combined echo).
const echoU = (uuid: string, texts: string[], opts: Record<string, unknown> = {}) => ({
  type: 'user',
  message: { role: 'user', content: texts.map((t) => ({ type: 'text', text: t })) },
  uuid,
  isReplay: true,
  ...opts,
});
const lifecycle = (commandUuid: string, state: string) => ({ type: 'command_lifecycle', command_uuid: commandUuid, state });
const requeued = (id: string, newId: string) => ({ type: 'x-optio-requeued', id, new_id: newId });
const delta = (text: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text } } });
const assistantText = (text: string, msgId?: string) => ({ type: 'assistant', message: { role: 'assistant', id: msgId, content: [{ type: 'text', text }] } });
const toolCall = (id: string, name: string, input: unknown) => ({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', id, name, input }] } });
const toolResult = (id: string, content: unknown, isError = false) => ({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content, is_error: isError }] } });
const result = (text: string) => ({ type: 'result', subtype: 'success', result: text });
const queued = (id: string, text: string) => ({ type: 'x-optio-queued', id, text });
const taken = (ids: string[]) => ({ type: 'x-optio-taken', ids });
const localUser = (text: string, id?: string, isQueued = false) => ({ type: 'x-optio-local-user', text, id, queued: isQueued });
const NOW = Date.parse('2026-09-13T17:49:00.000Z');

function run(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, NOW), from);
}
type UserItem = Extract<ChatItem, { kind: 'user' }>;
const users = (s: ChatState) => s.items.filter((i) => i.kind === 'user') as UserItem[];
// Kinds, with queued bubbles told apart.
const kinds = (s: ChatState) => s.items.map((i) => (i.kind === 'user' && i.queued ? 'queued' : i.kind));
const texts = (s: ChatState) => s.items.map((i) => ('text' in i ? i.text : i.kind));

describe('claudecode steering: queued bubbles', () => {
  it('x-optio-queued adds a queued bubble pinned at the bottom', () => {
    const s = run([user('q'), delta('ans'), queued('q1', 'steer')]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'queued']);
    expect(users(s)[1]).toMatchObject({ text: 'steer', queued: true, queueId: 'q1' });
  });

  it('the streaming answer keeps growing above a queued bubble: one bubble, not split, not repeated', () => {
    const s = run([user('q'), delta('Hel'), queued('q1', 'steer'), delta('lo'), assistantText('Hello', 'm1')]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'queued']);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1);
    expect(texts(s)[1]).toBe('Hello');
  });

  it('new rows go in front of queued bubbles', () => {
    const s = run([user('q'), queued('q1', 'steer'), toolCall('t1', 'Bash', { command: 'ls' })]);
    expect(kinds(s)).toEqual(['user', 'tool', 'queued']);
  });

  it('the echo takes the queued bubble to after the tool row it came with', () => {
    const s = run([
      user('q'), delta('para'), queued('q1', 'steer'), assistantText('para', 'm1'),
      toolCall('t1', 'Bash', {}), toolResult('t1', 'ok'), user('steer'),
      assistantText('more', 'm2'), result('more'),
    ]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'tool', 'user', 'assistant']);
    expect(users(s)[1]).toMatchObject({ text: 'steer', queueId: 'q1' });
    expect(users(s)[1].queued).toBeUndefined();
    expect(texts(s).filter((_, i) => s.items[i].kind === 'assistant')).toEqual(['para', 'more']);
  });

  it('an echo matching the second queued bubble moves only that one', () => {
    const s = run([user('q'), queued('q1', 'a'), queued('q2', 'b'), user('b')]);
    expect(kinds(s)).toEqual(['user', 'user', 'queued']);
    expect(users(s).map((u) => u.text)).toEqual(['q', 'b', 'a']);
  });

  it('x-optio-taken takes held messages by id, in order', () => {
    const s = run([user('q'), queued('q1', 'a'), queued('q2', 'b'), taken(['q1', 'q2'])]);
    expect(kinds(s)).toEqual(['user', 'user', 'user']);
    expect(users(s).map((u) => u.text)).toEqual(['q', 'a', 'b']);
  });

  it('the local echo and x-optio-queued of one message make one bubble, in either order', () => {
    const a = run([user('q'), localUser('steer', 'q1', true), queued('q1', 'steer')]);
    const b = run([user('q'), queued('q1', 'steer'), localUser('steer', 'q1', true)]);
    expect(users(a)).toHaveLength(2);
    expect(users(a)[1]).toMatchObject({ text: 'steer', queued: true, queueId: 'q1' });
    expect(users(a)[1].local).toBeUndefined();
    expect(a.items).toEqual(b.items);
  });

  it('a local echo arriving after its message was taken adds nothing', () => {
    const s = run([user('q'), queued('q1', 'steer'), user('steer'), localUser('steer', 'q1', true)]);
    expect(users(s)).toHaveLength(2);
    expect(kinds(s)).toEqual(['user', 'user']);
  });

  it('a message sent behind queued ones waits behind them', () => {
    const s = run([user('q'), queued('q1', 'a'), localUser('b', 's1')]);
    expect(kinds(s)).toEqual(['user', 'queued', 'queued']);
  });

  it('an idle send is a plain local bubble, as before', () => {
    const s = run([localUser('hi', 's1')]);
    expect(users(s)[0]).toMatchObject({ text: 'hi', local: true, queueId: 's1' });
    expect(users(s)[0].queued).toBeUndefined();
  });

  it('x-optio-queued carrying an upload notice shows only the prompt text, and its echo takes it', () => {
    const prompt = bundleUploadNotice(['uploads/pic.png'], 'review it');
    const mid = run([user('q'), queued('q1', prompt)]);
    expect(users(mid)[1]).toMatchObject({ text: 'review it', queued: true });
    const s = run([user(prompt)], mid);
    expect(kinds(s)).toEqual(['user', 'activity', 'user']);
    expect(users(s)[1]).toMatchObject({ text: 'review it', queueId: 'q1' });
  });

  it('session end turns an undelivered queued bubble into a muted note', () => {
    for (const end of [{ type: 'x-optio-closed', reason: 'x' }, { type: 'x-optio-resumed' }]) {
      const s = run([user('q'), queued('q1', 'steer'), end]);
      expect(users(s).some((u) => u.queued)).toBe(false);
      const note = s.items.find((i) => i.kind === 'activity');
      expect(note).toMatchObject({ kind: 'activity', text: 'Not delivered: steer', muted: true });
    }
  });
});

const interrupt = { type: 'x-optio-interrupt', by: 'user' };
const aborted = (reason = 'aborted_streaming') => ({ type: 'result', subtype: 'error_during_execution', is_error: true, terminal_reason: reason });

describe('claudecode steering: operator interrupts', () => {
  it('while idle adds nothing', () => {
    expect(run([interrupt])).toEqual(initialChatState);
  });

  it('cuts off the streaming answer, adds one muted row and swallows the CLI artefacts', () => {
    const s = run([
      user('q'), delta('Light'), interrupt, delta('house'),
      assistantText('Lighthouse', 'm1'), user('[Request interrupted by user]'), aborted(),
    ]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'activity']);
    expect(s.items[1]).toMatchObject({ kind: 'assistant', text: 'Lighthouse', pending: false, interrupted: true, msgId: 'm1' });
    expect('openPart' in s.items[1]).toBe(false);
    expect(s.items[2]).toMatchObject({ kind: 'activity', text: '⏹ Interrupted by you', muted: true });
    expect(s.busy).toBe(false);
    expect(s.interrupt).toBeUndefined();
  });

  it('a second interrupt before the turn ends adds no second row', () => {
    const s = run([user('q'), delta('a'), interrupt, interrupt]);
    expect(s.items.filter((i) => i.kind === 'activity')).toHaveLength(1);
  });

  it('queued bubbles stay pinned below the interrupt row', () => {
    const s = run([user('q'), delta('ans'), queued('q1', 'later'), interrupt]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'activity', 'queued']);
    expect(s.items[1]).toMatchObject({ interrupted: true });
  });

  it('a running tool stops, and the CLI rejection is not stored as its result', () => {
    const s = run([
      user('q'), toolCall('t1', 'Bash', { command: 'sleep 25' }), interrupt,
      toolResult('t1', "The user doesn't want to proceed with this tool use.", true),
      user('[Request interrupted by user for tool use]'), aborted('aborted_tools'),
    ]);
    const tool = s.items.find((i) => i.kind === 'tool') as Extract<ChatItem, { kind: 'tool' }>;
    expect(tool.status).toBe('stopped');
    expect(tool.result).toBeUndefined();
    expect(s.items.some((i) => i.kind === 'error')).toBe(false);
    expect(users(s).map((u) => u.text)).toEqual(['q']);
  });

  it('the flag ends at the result, so the next turn streams normally', () => {
    const s = run([user('q'), delta('a'), interrupt, aborted(), user('next'), delta('b')]);
    expect(s.interrupt).toBeUndefined();
    expect(s.items[s.items.length - 1]).toMatchObject({ kind: 'assistant', text: 'b', pending: true });
  });

  it('an error result other than an abort still shows after an interrupt', () => {
    const s = run([user('q'), interrupt, { type: 'result', subtype: 'error_during_execution', is_error: true, terminal_reason: 'model_error', result: 'boom' }]);
    expect(s.items.some((i) => i.kind === 'error')).toBe(true);
  });

  it('without x-optio-interrupt the CLI artefacts still show (an interrupt optio did not send)', () => {
    const s = run([user('q'), delta('a'), assistantText('a', 'm1'), user('[Request interrupted by user]'), aborted()]);
    expect(users(s).map((u) => u.text)).toContain('[Request interrupted by user]');
    expect(s.items.some((i) => i.kind === 'error')).toBe(true);
  });

  it('a foreground task the CLI reports stopped stays a foreground row', () => {
    const s = run([
      toolCall('t1', 'Bash', { command: 'sleep 25' }),
      { type: 'system', subtype: 'task_started', task_id: 'k1', tool_use_id: 't1', is_backgrounded: false, task_type: 'local_bash' },
      { type: 'system', subtype: 'task_notification', task_id: 'k1', tool_use_id: 't1', status: 'stopped', summary: 'sleep 25' },
    ]);
    const tool = s.items.find((i) => i.kind === 'tool') as Extract<ChatItem, { kind: 'tool' }>;
    expect(tool).toMatchObject({ taskId: 'k1', status: 'stopped' });
    expect(tool.background).toBeUndefined();
    expect(tool.result).toBeUndefined();
  });

  it('session close during an interrupt clears the flag', () => {
    const s = run([user('q'), delta('a'), interrupt, { type: 'x-optio-closed', reason: 'x' }]);
    expect(s.interrupt).toBeUndefined();
    expect('openPart' in s.items[1]).toBe(false);
  });
});

// Fix round 1: x-optio-interrupt raced the end of a turn the CLI still
// finished normally (a non-abort result) -- reproduced live with running,
// user 'q', delta 'Hel', x-optio-interrupt, assistant 'Hello.', result
// success 'Hello.', idle. OWNER RULING: render exactly as if optio had never
// sent the interrupt.
describe('claudecode steering: a too-late interrupt (marker raced a normal result)', () => {
  const idle = { type: 'system', subtype: 'session_state_changed', state: 'idle' };

  function withoutSeq(items: ChatItem[]): unknown[] {
    return items.map(({ seq: _seq, ...rest }) => rest);
  }
  function live(events: any[]): ChatState {
    return run(events);
  }
  function replay(events: any[]): ChatState {
    return run(events.filter((e) => e.type !== 'stream_event'));
  }

  it('the answer bubble ends complete, not interrupted, with no duplicate and no Interrupted by you row', () => {
    const s = run([user('q'), delta('Hel'), interrupt, assistantText('Hello.', 'm1'), result('Hello.'), idle]);
    expect(kinds(s)).toEqual(['user', 'assistant']);
    expect(s.items[1]).toMatchObject({ kind: 'assistant', text: 'Hello.', pending: false, msgId: 'm1' });
    expect('interrupted' in s.items[1]).toBe(false);
    expect(s.busy).toBe(false);
    expect(s.interrupt).toBeUndefined();
  });

  it('matches the same stream without the marker, live and on replay (streaming answer)', () => {
    const withMarker = [user('q'), delta('Hel'), interrupt, assistantText('Hello.', 'm1'), result('Hello.'), idle];
    const withoutMarker = withMarker.filter((e) => e.type !== 'x-optio-interrupt');
    expect(withoutSeq(live(withMarker).items)).toEqual(withoutSeq(live(withoutMarker).items));
    expect(withoutSeq(replay(withMarker).items)).toEqual(withoutSeq(replay(withoutMarker).items));
  });

  it('matches the same stream without the marker, live and on replay (a tool running when it arrives)', () => {
    const withMarker = [
      user('q'), toolCall('t1', 'Bash', { command: 'sleep 1' }), interrupt,
      toolResult('t1', 'done', false), result('OK.'), idle,
    ];
    const withoutMarker = withMarker.filter((e) => e.type !== 'x-optio-interrupt');
    expect(withoutSeq(live(withMarker).items)).toEqual(withoutSeq(live(withoutMarker).items));
    expect(withoutSeq(replay(withMarker).items)).toEqual(withoutSeq(replay(withoutMarker).items));
    const s = live(withMarker);
    const tool = s.items.find((i) => i.kind === 'tool') as Extract<ChatItem, { kind: 'tool' }>;
    expect(tool.status).toBe('done');
    expect(tool.result).toBe('done');
  });

  it('an abort result (a genuine interrupt) still keeps today\'s interrupt rendering', () => {
    const s = run([user('q'), delta('a'), interrupt, aborted()]);
    expect(s.items.some((i) => i.kind === 'activity' && i.text === '⏹ Interrupted by you')).toBe(true);
    expect(s.items[1]).toMatchObject({ interrupted: true });
  });

  // Fix round 2: undoInterrupt used to pattern-match `interrupted`/`stopped`
  // across the WHOLE item list, so a too-late race in a later turn also
  // reverted an earlier turn's own, already-finalized genuine interrupt.
  // Repro (from the finding): turn 1 aborts for real; turn 2's marker then
  // races a normal result.
  it('a too-late interrupt in one turn does not corrupt an earlier turn\'s own genuine interrupt', () => {
    const s = run([
      user('t1'), delta('a'), interrupt, aborted(),
      user('t2'), delta('b'), interrupt, result('b'),
    ]);
    expect(kinds(s)).toEqual(['user', 'assistant', 'activity', 'user', 'assistant']);
    // Turn 1's bubble and its row: untouched, still permanently interrupted.
    expect(s.items[1]).toMatchObject({ text: 'a', pending: false, interrupted: true });
    expect(s.items[2]).toMatchObject({ kind: 'activity', text: '⏹ Interrupted by you' });
    // Turn 2's too-late race: renders as if optio never sent it -- finalized
    // in place, not left pending, not marked interrupted, no second row.
    expect(s.items[4]).toMatchObject({ text: 'b', pending: false });
    expect('interrupted' in s.items[4]).toBe(false);
    expect(s.busy).toBe(false);
    expect(s.interrupt).toBeUndefined();
  });

  // Same shape, but the earlier turn's interrupt stopped a running tool
  // (permanently `status:'stopped'`, no result) instead of cutting off text.
  it('a too-late interrupt does not revive an earlier turn\'s own genuinely-stopped tool row', () => {
    const s = run([
      user('t1'), toolCall('t1', 'Bash', { command: 'sleep 25' }), interrupt,
      toolResult('t1', "The user doesn't want to proceed with this tool use.", true),
      user('[Request interrupted by user for tool use]'), aborted('aborted_tools'),
      user('t2'), delta('b'), interrupt, result('b'),
    ]);
    const tool = s.items.find((i) => i.kind === 'tool') as Extract<ChatItem, { kind: 'tool' }>;
    expect(tool).toMatchObject({ status: 'stopped' });
    expect(tool.result).toBeUndefined();
    const answer = s.items[s.items.length - 1];
    expect(answer).toMatchObject({ kind: 'assistant', text: 'b', pending: false });
    expect('interrupted' in answer).toBe(false);
  });
});

// final-review M3: Steering.interrupt_and_send now emits x-optio-queued for
// its own text (same id /steer returns), so the view's local echo (queued:
// false — it is not pinned, just an immediate confirmation) and the wire's
// own echo of the sent text both have an id-bearing bubble to land on,
// whichever of the two arrives first. `base` uses the REAL wire order (fix
// 8 review round 1: x-optio-interrupt, then the aborted result, THEN
// x-optio-queued — interrupt_and_send waits for the turn end before
// emitting it — not queued-then-result, which the round-1 tests below used
// and which cannot show the bug because the agent still reads as busy then).
describe('claudecode steering: final-review M3 (interrupt-and-send announces its own text)', () => {
  const base = [user('q'), delta('a'), interrupt, aborted(), queued('q1', 'now')];

  it('confirms into ONE bubble, never "Not delivered", local echo first', () => {
    const s = run([...base, localUser('now', 'q1', false), user('now')]);
    const bubbles = users(s).filter((u) => u.text === 'now');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].queued).toBeUndefined();
    expect(bubbles[0].local).toBeUndefined();
    expect(s.items.some((i) => i.kind === 'activity' && i.text.startsWith('Not delivered'))).toBe(false);
  });

  it('confirms into ONE bubble, never "Not delivered", wire echo first', () => {
    const s = run([...base, user('now'), localUser('now', 'q1', false)]);
    const bubbles = users(s).filter((u) => u.text === 'now');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].queued).toBeUndefined();
    expect(bubbles[0].local).toBeUndefined();
    expect(s.items.some((i) => i.kind === 'activity' && i.text.startsWith('Not delivered'))).toBe(false);
  });
});

// Review of fix 8, finding 1: x-optio-queued for a steer's OWN text arrives
// (in the real order above) while the agent already reads as idle again —
// the interrupted turn's result already cleared `busy`. Before this fix,
// addQueued always set queued:true regardless, so the message rendered as
// a pinned "Queued — the agent reads it when ready · Send now" bubble for
// the ~1.5-2s until the CLI's own wire echo arrived, even though it had
// already been sent: clicking "Send now" there posts an empty /steer and
// interrupts the very turn this message just started.
describe('claudecode steering: review of fix 8 (a steer never shows as Queued/Send now)', () => {
  const base = [user('q'), delta('a'), interrupt, aborted()];

  it('x-optio-queued alone (idle) is a plain unconfirmed bubble, not queued', () => {
    const s = run([...base, queued('q1', 'now')]);
    const bubble = users(s).find((u) => u.text === 'now');
    expect(bubble).toBeDefined();
    expect(bubble!.queued).toBeUndefined();
    expect(bubble!.queueId).toBe('q1');
  });

  it('stays un-queued between x-optio-queued and the local echo, local echo first', () => {
    const s = run([...base, queued('q1', 'now'), localUser('now', 'q1', false)]);
    const bubble = users(s).find((u) => u.text === 'now');
    expect(bubble!.queued).toBeUndefined();
  });

  it('a genuine Send when ready queued while busy is unaffected: still pinned, offers Send now', () => {
    const s = run([user('q'), delta('ans'), queued('q1', 'later')]);
    const bubble = users(s).find((u) => u.text === 'later');
    expect(bubble!.queued).toBe(true);
  });
});

// Fix 13b, owner ruling 2026-09-15: the UI moves queued bubbles by the CLI's
// own command_lifecycle events (by uuid) instead of matching echo text; text
// matching stays only as a fallback for conversations without uuids. See
// cli-queue-lifecycle.md for the event shapes and orderings.
describe('claudecode steering: lifecycle-driven queued bubbles (Fix 13b)', () => {
  it("command_lifecycle 'started' takes a queued bubble, ahead of its echo", () => {
    const s = run([user('q'), queued('q1', 'later'), lifecycle('q1', 'started')]);
    expect(kinds(s)).toEqual(['user', 'user']);
    expect(users(s)[1]).toMatchObject({ text: 'later', queueId: 'q1' });
    expect(users(s)[1].queued).toBeUndefined();
  });

  it("a later echo carrying the same uuid confirms in place, no duplicate, and backfills the timestamp", () => {
    const s = run([user('q'), queued('q1', 'later'), lifecycle('q1', 'started'), echoU('q1', ['later'], { timestamp: '2026-09-13T17:49:05.000Z' })]);
    expect(users(s)).toHaveLength(2);
    expect(users(s)[1]).toMatchObject({ text: 'later', timestamp: Date.parse('2026-09-13T17:49:05.000Z') });
  });

  it("'started' for an id with no matching item is a harmless no-op (an idle send, or agent feedback)", () => {
    const s = run([lifecycle('s1', 'started'), lifecycle('s1', 'queued')]);
    expect(s).toEqual(initialChatState);
  });

  it("'queued' and 'completed' states are no-ops beyond the pinning x-optio-queued already did", () => {
    const s = run([user('q'), queued('q1', 'later'), lifecycle('q1', 'queued')]);
    expect(users(s)[1].queued).toBe(true);
    const s2 = run([user('q'), queued('q1', 'later'), lifecycle('q1', 'started'), lifecycle('q1', 'completed')]);
    expect(users(s2)[1].queued).toBeUndefined();
  });

  it("an echo taking the bubble ahead of 'started': the later 'started' is then a no-op", () => {
    const s = run([user('q'), queued('q1', 'later'), echoU('q1', ['later']), lifecycle('q1', 'started')]);
    expect(users(s)).toHaveLength(2);
    expect(kinds(s)).toEqual(['user', 'user']);
  });

  it("'cancelled' for a still-queued bubble optio did not re-queue marks it Not delivered", () => {
    const s = run([user('q'), queued('q1', 'later'), lifecycle('q1', 'cancelled')]);
    expect(users(s).some((u) => u.queued)).toBe(false);
    const note = s.items.find((i) => i.kind === 'activity');
    expect(note).toMatchObject({ kind: 'activity', text: 'Not delivered: later', muted: true, queueId: 'q1' });
  });

  it("'discarded'/'refused' for a still-queued bubble behave the same as 'cancelled'", () => {
    for (const state of ['discarded', 'refused']) {
      const s = run([user('q'), queued('q1', 'later'), lifecycle('q1', state)]);
      expect(s.items.find((i) => i.kind === 'activity')).toMatchObject({ text: 'Not delivered: later' });
    }
  });

  it("'cancelled' for an id already taken (a genuinely interrupted, already-delivered message) does not touch its bubble", () => {
    const s = run([user('q'), queued('q1', 'later'), lifecycle('q1', 'started'), lifecycle('q1', 'cancelled')]);
    expect(users(s)[1]).toMatchObject({ text: 'later' });
    expect(s.items.some((i) => i.kind === 'activity')).toBe(false);
  });

  it("x-optio-requeued re-keys a still-queued bubble in place, staying queued", () => {
    const s = run([user('q'), queued('q1', 'later'), requeued('q1', 'q1-new')]);
    expect(users(s)[1]).toMatchObject({ text: 'later', queued: true, queueId: 'q1-new' });
  });

  it("x-optio-requeued undoes an already-applied Not delivered note (steering.py emits 'cancelled' well before the requeue): ignored, not dropped", () => {
    const s = run([
      user('q'), queued('q1', 'later'), lifecycle('q1', 'cancelled'), requeued('q1', 'q1-new'),
    ]);
    expect(s.items.some((i) => i.kind === 'activity')).toBe(false);
    const bubble = users(s).find((u) => u.queueId === 'q1-new');
    expect(bubble).toMatchObject({ text: 'later', queued: true });
  });

  it('the re-keyed bubble is later taken by started/echo under its NEW id, same as any other queued bubble', () => {
    const s = run([
      user('q'), queued('q1', 'later'), lifecycle('q1', 'cancelled'), requeued('q1', 'q1-new'),
      lifecycle('q1-new', 'started'), echoU('q1-new', ['later'], { timestamp: '2026-09-13T17:49:05.000Z' }),
    ]);
    expect(kinds(s)).toEqual(['user', 'user']);
    expect(users(s)[1]).toMatchObject({ text: 'later', timestamp: Date.parse('2026-09-13T17:49:05.000Z') });
    expect(users(s)[1].queued).toBeUndefined();
  });

  // "a message taken at a tool boundary" (cli-queue-lifecycle.md case b):
  // two queued messages, one WITH a uuid (lifecycle-driven), one WITHOUT
  // (fallback text matching) -- both must resolve, side by side.
  it('a fold of one uuid-driven and one legacy (no-uuid) queued message both resolve at a tool boundary', () => {
    const s = run([
      user('q'), toolCall('t1', 'Bash', {}), queued('q1', 'apple'), queued('s1', 'banana'),
      toolResult('t1', 'ok'),
      lifecycle('q1', 'started'), echoU('q1', ['apple']), user('banana'),
    ]);
    expect(kinds(s)).toEqual(['user', 'tool', 'user', 'user']);
    expect(texts(s).slice(2)).toEqual(['apple', 'banana']);
    expect(users(s).some((u) => u.queued)).toBe(false);
  });

  // "a fold of 3" (cli-queue-lifecycle.md case c): three queued while
  // streaming, taken together after the turn ends. The first N-1 echoes
  // arrive solo (own uuid, own text, no timestamp) and 'started' for all
  // three arrives interleaved with them; only the LAST message's combined
  // echo (all 3 texts, its own uuid, a timestamp) arrives last.
  it('a fold of 3: bubbles move at their own started/solo-echo, the combined echo confirms the last one and adds no duplicates', () => {
    const s = run([
      user('q'), delta('essay'), queued('q1', 'apple'), queued('q2', 'banana'), queued('q3', 'cherry'),
      assistantText('essay', 'm1'), result('essay'),
      echoU('q1', ['apple']), echoU('q2', ['banana']),
      lifecycle('q1', 'started'), lifecycle('q2', 'started'), lifecycle('q3', 'started'),
      echoU('q3', ['apple', 'banana', 'cherry'], { timestamp: '2026-09-13T17:49:10.000Z' }),
      assistantText('ok', 'm2'), result('ok'),
    ]);
    expect(texts(s)).toEqual(['q', 'essay', 'apple', 'banana', 'cherry', 'ok']);
    expect(users(s).some((u) => u.queued)).toBe(false);
    expect(users(s).find((u) => u.text === 'cherry')).toMatchObject({ timestamp: Date.parse('2026-09-13T17:49:10.000Z') });
  });

  it('live (with the streamed delta) and replay (without) give the same items for the fold of 3', () => {
    const withDelta = [
      user('q'), delta('essay'), queued('q1', 'apple'), queued('q2', 'banana'), queued('q3', 'cherry'),
      assistantText('essay', 'm1'), result('essay'),
      echoU('q1', ['apple']), echoU('q2', ['banana']),
      lifecycle('q1', 'started'), lifecycle('q2', 'started'), lifecycle('q3', 'started'),
      echoU('q3', ['apple', 'banana', 'cherry'], { timestamp: '2026-09-13T17:49:10.000Z' }),
      assistantText('ok', 'm2'), result('ok'),
    ];
    const replayEvents = withDelta.filter((e) => e.type !== 'stream_event');
    const live = run(withDelta);
    const replay = run(replayEvents);
    const strip = (s: ChatState) => s.items.map(({ seq: _seq, ...rest }) => rest);
    expect(strip(live)).toEqual(strip(replay));
  });

  // Cancel + interrupt + re-send (cli-queue-lifecycle.md case d / q-cancel3):
  // message 3's uuid is cancelled before ever starting, the turn is
  // interrupted, messages 1-2 run as the next turn, and once message 2 (the
  // Send-now target) is seen 'started', message 3 is re-sent with a new
  // uuid and x-optio-requeued re-keys its still-queued bubble.
  it('cancel3: message 3 survives cancel+interrupt+resend, ends up delivered under its re-keyed id, never left Not delivered or duplicated', () => {
    const events = [
      user('q'), delta('essay'), queued('q1', 'apple'), queued('q2', 'banana'), queued('q3', 'cherry'),
      lifecycle('q1', 'queued'), lifecycle('q2', 'queued'), lifecycle('q3', 'queued'),
      { type: 'x-optio-interrupt', by: 'user' },
      lifecycle('q3', 'cancelled'),
      assistantText('essay', 'm0'),
      { type: 'result', subtype: 'error_during_execution', is_error: true, terminal_reason: 'aborted_streaming' },
      echoU('q1', ['apple']), lifecycle('q1', 'started'), lifecycle('q2', 'started'),
      requeued('q3', 'q3-new'),
      echoU('q2', ['apple', 'banana'], { timestamp: '2026-09-13T17:49:08.000Z' }),
      assistantText('got it', 'm1'), result('got it'),
      lifecycle('q3-new', 'started'), echoU('q3-new', ['cherry'], { timestamp: '2026-09-13T17:49:10.000Z' }),
      assistantText('cherry ok', 'm2'), result('cherry ok'),
    ];
    const s = run(events);
    // Every message resolves, none duplicated, nothing left queued or noted
    // undelivered (the "cancelled that belongs to our own requeue" rule).
    expect(texts(s).filter((t) => t === 'apple')).toEqual(['apple']);
    expect(texts(s).filter((t) => t === 'banana')).toEqual(['banana']);
    expect(texts(s).filter((t) => t === 'cherry')).toEqual(['cherry']);
    expect(users(s).some((u) => u.queued)).toBe(false);
    expect(s.items.some((i) => i.kind === 'activity' && i.text.startsWith('Not delivered'))).toBe(false);
    expect(users(s).find((u) => u.text === 'cherry')).toMatchObject({ timestamp: Date.parse('2026-09-13T17:49:10.000Z') });
    // Live (nothing to strip here -- no stream_event in this hand-built
    // sequence beyond the one delta) matches a pure replay of the same
    // events with that delta removed.
    const replayEvents = events.filter((e) => e.type !== 'stream_event');
    const strip = (st: ChatState) => st.items.map(({ seq: _seq, ...rest }) => rest);
    expect(strip(s)).toEqual(strip(run(replayEvents)));
  });

  it('the legacy fallback (no uuid, no lifecycle events at all) keeps matching by text', () => {
    // "No uuid -> no lifecycle events, ever" (cli-queue-lifecycle.md §4): an
    // older conversation's queued bubble is taken purely by its text-based
    // echo, exactly as before Fix 13b.
    const s = run([user('q'), queued('s1', 'later'), user('later')]);
    expect(kinds(s)).toEqual(['user', 'user']);
    expect(users(s)[1]).toMatchObject({ text: 'later', queueId: 's1' });
    expect(users(s)[1].queued).toBeUndefined();
  });
});
