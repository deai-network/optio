import { describe, it, expect } from 'vitest';
import { initialChatState, reduceEvent } from '../events.js';
import type { ChatItem, ChatState } from '../events.js';

// -- raw stream-json event builders (wire shapes verified in Phase I) --------

const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
const assistantText = (text: string) => ({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text }] } });
const toolUse = (name: string, input: unknown) => ({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', name, input }] } });
const delta = (text: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text } } });
const result = (text: string) => ({ type: 'result', subtype: 'success', result: text });
const controlRequest = (requestId: string, toolName: string, input: unknown) => ({
  type: 'control_request',
  request_id: requestId,
  request: { subtype: 'can_use_tool', tool_name: toolName, input },
});

function run(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1), from);
}

function ofKind<K extends ChatItem['kind']>(state: ChatState, kind: K): Extract<ChatItem, { kind: K }>[] {
  return state.items.filter((i) => i.kind === kind) as Extract<ChatItem, { kind: K }>[];
}

const cases: { name: string; events: any[]; check: (s: ChatState) => void }[] = [
  {
    name: 'user event becomes a user bubble and marks busy',
    events: [user('hi there')],
    check: (s) => {
      expect(ofKind(s, 'user')).toEqual([{ kind: 'user', text: 'hi there', seq: 1 }]);
      expect(s.busy).toBe(true);
    },
  },
  {
    name: '"System: "-prefixed user text becomes an activity row, not a bubble',
    events: [user('System: session resumed')],
    check: (s) => {
      expect(ofKind(s, 'user')).toEqual([]);
      expect(ofKind(s, 'activity')).toHaveLength(1);
      expect(ofKind(s, 'activity')[0].text).toContain('System: session resumed');
    },
  },
  {
    name: 'assistant text + result finalizes a single bubble and clears busy',
    events: [user('q'), assistantText('Answer'), result('Answer')],
    check: (s) => {
      const bubbles = ofKind(s, 'assistant');
      expect(bubbles).toHaveLength(1);
      expect(bubbles[0].text).toBe('Answer');
      expect(bubbles[0].pending).toBe(false);
      expect(s.busy).toBe(false);
    },
  },
  {
    name: 'tool_use content block becomes an activity row with a one-line preview',
    events: [toolUse('Bash', { command: 'ls -la' })],
    check: (s) => {
      const activity = ofKind(s, 'activity');
      expect(activity).toHaveLength(1);
      expect(activity[0].text).toMatch(/^running Bash/);
      expect(activity[0].text).toContain('ls -la');
    },
  },
  {
    name: 'control_request becomes an unanswered permission card; busy stays true',
    events: [user('do it'), controlRequest('req-1', 'Bash', { command: 'rm -rf /tmp/x' })],
    check: (s) => {
      const cards = ofKind(s, 'permission');
      expect(cards).toHaveLength(1);
      expect(cards[0]).toMatchObject({ requestId: 'req-1', toolName: 'Bash', input: { command: 'rm -rf /tmp/x' }, answered: null });
      expect(s.busy).toBe(true);
    },
  },
  {
    name: 'x-optio-permission-answered marks the matching card answered',
    events: [
      controlRequest('req-1', 'Bash', { command: 'ls' }),
      { type: 'x-optio-permission-answered', request_id: 'req-1', behavior: 'allow' },
    ],
    check: (s) => {
      expect(ofKind(s, 'permission')).toHaveLength(1);
      expect(ofKind(s, 'permission')[0].answered).toBe('allow');
    },
  },
  {
    name: 'x-optio-closed appends a closed item and flips closed',
    events: [{ type: 'x-optio-closed', reason: 'process exited' }],
    check: (s) => {
      expect(ofKind(s, 'closed')).toEqual([{ kind: 'closed', reason: 'process exited', seq: 1 }]);
      expect(s.closed).toBe(true);
    },
  },
  {
    name: 'unhandled event types are ignored',
    events: [{ type: 'system', subtype: 'init' }, { type: 'x-optio-unparseable', line: '???' }],
    check: (s) => {
      expect(s.items).toEqual([]);
      expect(s).toEqual(initialChatState);
    },
  },
];

describe('reduceEvent', () => {
  for (const c of cases) it(c.name, () => c.check(run(c.events)));

  it('stream_event deltas accumulate into a pending bubble, then result replaces the text', () => {
    const mid = run([user('q'), delta('Hel'), delta('lo')]);
    const pending = ofKind(mid, 'assistant');
    expect(pending).toHaveLength(1);
    expect(pending[0].text).toBe('Hello');
    expect(pending[0].pending).toBe(true);
    expect(mid.busy).toBe(true);

    const done = reduceEvent(mid, result('Hello world'), 4);
    const bubbles = ofKind(done, 'assistant');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].text).toBe('Hello world');
    expect(bubbles[0].pending).toBe(false);
    expect(done.busy).toBe(false);
  });

  it('does not mutate the input state', () => {
    const before = run([user('q')]);
    const frozen = JSON.parse(JSON.stringify(before));
    reduceEvent(before, assistantText('Answer'), 2);
    expect(before).toEqual(frozen);
  });
});
