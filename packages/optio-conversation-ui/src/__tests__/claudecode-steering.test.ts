import { describe, expect, it } from 'vitest';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';
import { bundleUploadNotice } from '../uploads.js';

// Steering events through the claudecode reducer. Wire builders as in
// claudecode-events.test.ts; x-optio-* are the listener's synthetic events
// and the view's local echo.
const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
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
// whichever of the two arrives first.
describe('claudecode steering: final-review M3 (interrupt-and-send announces its own text)', () => {
  const base = [user('q'), delta('a'), interrupt, queued('q1', 'now'), aborted()];

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
