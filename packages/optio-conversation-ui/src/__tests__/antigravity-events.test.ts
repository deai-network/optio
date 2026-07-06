import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatItem, type ChatState } from '../chat.js';
import { reduceAntigravityEvent } from '../antigravity/events.js';

// The antigravity reducer consumes the RAW transcript.jsonl events the listener
// fans out over SSE — Antigravity has NO live transport, so a "turn" is
// synthesised from one `agy -p` invocation + the structured transcript file
// (optio-antigravity conversation.py). Each transcript line is a dict with a
// `type` in {user, assistant, tool, …} plus a `conversationId`; there is no
// token streaming (one assistant line = the whole coalesced answer for that
// turn) and no separate turn-end frame (the assistant line IS the turn end).
// The synthetic x-optio-* events (local echo / control update / permission
// answered / closed) are shared with every other engine.
//
// TODO(S3): the transcript schema here (user/assistant/tool line shapes) tracks
// fake_agy.py's documented minimal shape; reconcile with the real captured
// transcript fixture once the S3 spike runs (see antigravity-real-wire.test.ts).

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceAntigravityEvent(s, ev, i), from);
}

const user = (text: string) => ({ type: 'user', conversationId: 'c1', text });
const assistant = (text: string) => ({ type: 'assistant', conversationId: 'c1', text });
const tool = (name: string, input: unknown) => ({
  type: 'tool', conversationId: 'c1', name, input,
});

describe('antigravity transcript event reducer', () => {
  it('an assistant transcript line renders a coalesced, finalized answer bubble and clears busy', () => {
    // One `agy -p` turn delivers the whole answer at once (no streaming, no
    // turn-end frame) — the assistant line is both the answer and the turn end.
    const s = play([{ type: 'x-optio-local-user', text: 'say PONG' }, assistant('PONG')]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });

  it('a full turn: user → tool → assistant yields one bubble, a persisted tool row, busy cleared', () => {
    const s = play([user('do the thing'), tool('read_file', { path: 'README' }), assistant('Done reading.')]);
    const kinds = s.items.map((i) => i.kind);
    expect(kinds).toEqual(['user', 'tool', 'assistant']);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1);
    expect((bubbles[0] as any).text).toBe('Done reading.');
    expect((bubbles[0] as any).pending).toBe(false);
    expect(s.busy).toBe(false);
  });

  it('a tool row persists (transcript history) and carries its full input for verbose KV rendering', () => {
    // Unlike a live progress indicator, a transcript tool call is a durable part
    // of the record: the answer bubble must NOT drop it.
    const s = play([tool('shell', { command: 'grep -r x .', cwd: '/w' }), assistant('ok')]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.name).toBe('shell');
    expect(t && t.kind === 'tool' && t.input).toEqual({ command: 'grep -r x .', cwd: '/w' });
    // still present after the answer arrived
    expect(s.items.some((i) => i.kind === 'tool')).toBe(true);
  });

  it('the wire user echo confirms the optimistic local bubble in place (no duplicate)', () => {
    // The view optimistically renders x-optio-local-user on send; the transcript
    // then replays a `user` line for the same turn. Rendering both would double
    // the operator's message — dedupe by text (FIFO, sends echo in order).
    const s = play([{ type: 'x-optio-local-user', text: 'hello' }, user('hello')]);
    const users = s.items.filter((i) => i.kind === 'user');
    expect(users).toHaveLength(1);
    // the confirmed bubble is no longer flagged local
    expect((users[0] as any).local).toBeUndefined();
  });

  it('x-optio-local-user renders an optimistic user bubble and sets busy', () => {
    const s = play([{ type: 'x-optio-local-user', text: 'hello' }]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('hello');
    expect(u && u.kind === 'user' && u.local).toBe(true);
    expect(s.busy).toBe(true);
  });

  it('a second turn opens a fresh answer bubble instead of appending to the first', () => {
    const s = play([user('first'), assistant('one'), user('second'), assistant('two')]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['one', 'two']);
    expect(s.busy).toBe(false);
  });

  it('x-optio-control-update folds a model pick into state.controls', () => {
    const from: ChatState = {
      ...initialChatState,
      controls: [{ id: 'model', kind: 'select', label: 'Model', value: 'gemini-a', options: [] }],
    };
    const s = play([{ type: 'x-optio-control-update', id: 'model', value: 'gemini-b' }], from);
    expect(s.controls.find((c) => c.id === 'model')?.value).toBe('gemini-b');
  });

  it('x-optio-closed appends a closed divider and ends the session', () => {
    const s = play([user('hi'), assistant('bye'), { type: 'x-optio-closed', reason: 'process ended' }]);
    expect(s.closed).toBe(true);
    expect(s.busy).toBe(false);
    expect(s.items.some((i) => i.kind === 'closed')).toBe(true);
  });

  it('an unparseable transcript line is ignored (forward-compat no-op)', () => {
    const s = play([{ type: 'x-optio-unparseable', line: '{bad' }, assistant('still fine')]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1);
  });
});
