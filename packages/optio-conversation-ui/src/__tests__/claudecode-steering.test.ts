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
