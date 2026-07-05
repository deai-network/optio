import { describe, it, expect } from 'vitest';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';

// -- raw stream-json event builders (wire shapes verified in Phase I) --------

const user = (text: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text }] } });
const assistantText = (text: string, msgId?: string) => ({ type: 'assistant', message: { role: 'assistant', id: msgId, content: [{ type: 'text', text }] } });
const toolUse = (name: string, input: unknown) => ({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'tool_use', name, input }] } });
const delta = (text: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text } } });
const messageStart = (msgId: string) => ({ type: 'stream_event', event: { type: 'message_start', message: { id: msgId } } });
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
    name: 'tool_use content block becomes a tool item carrying structured input',
    events: [toolUse('Bash', { command: 'ls -la' })],
    check: (s) => {
      const tools = ofKind(s, 'tool');
      expect(tools).toHaveLength(1);
      expect(tools[0].name).toBe('Bash');
      expect(tools[0].input).toEqual({ command: 'ls -la' });
    },
  },
  {
    name: 'a new tool announcement supersedes the previous one (ephemeral)',
    events: [toolUse('ToolSearch', { query: 'x' }), toolUse('WebSearch', { query: 'y' })],
    check: (s) => {
      const tools = ofKind(s, 'tool');
      expect(tools).toHaveLength(1);
      expect(tools[0].name).toBe('WebSearch');
    },
  },
  {
    name: 'a permission request clears any in-flight tool announcement',
    events: [
      toolUse('WebSearch', { query: 'y' }),
      controlRequest('perm-1', 'WebSearch', { query: 'y' }),
    ],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(0);
      expect(ofKind(s, 'permission')).toHaveLength(1);
    },
  },
  {
    name: 'assistant answer text clears the in-flight tool announcement',
    events: [toolUse('Read', { file_path: '/x' }), assistantText('here is the answer')],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(0);
      expect(ofKind(s, 'assistant')).toHaveLength(1);
    },
  },
  {
    name: 'a trailing tool use (e.g. echo DONE) is cleared by session close',
    events: [toolUse('Bash', { command: 'echo DONE >> ./optio.log' }), { type: 'x-optio-closed', reason: 'process ended' }],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(0);
      expect(ofKind(s, 'closed')).toHaveLength(1);
    },
  },
  {
    name: 'result clears a lingering tool announcement',
    events: [toolUse('Bash', { command: 'x' }), result('done')],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(0);
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

  it('orders the question before the answer even when the answer streams first', () => {
    // With --replay-user-messages Claude streams the whole answer BEFORE
    // echoing the user message, so the streaming assistant bubble already
    // exists when the user echo arrives. The reducer must slot the user turn
    // in FRONT of the pending assistant bubble — array order, no seq sort.
    let s = initialChatState;
    s = reduceEvent(s, delta('partial answer'), 10); // answer streams first
    s = reduceEvent(s, user('the question'), 23); // echo arrives later (higher seq)
    s = reduceEvent(s, result('full answer'), 33);

    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant']);
    expect((s.items[0] as Extract<ChatItem, { kind: 'user' }>).text).toBe('the question');
    expect((s.items[1] as Extract<ChatItem, { kind: 'assistant' }>).text).toBe('full answer');
  });

  it('keeps chronological order when a turn never gets a result (buffer replay)', () => {
    // Replay of a session captured mid-turn (interrupt / close-on-DONE): no
    // result event ever finalizes the bubble. Later assistant texts must NOT
    // overwrite the stale pending bubble in place — items appended after it
    // (System: activity rows) would otherwise end up ABOVE newer answers.
    const s = run([
      user('orange'),
      assistantText('Failed delivery attempt.', 'msg_1'),
      user('System: deliverable mission-report.txt: always finish with "over and out".'),
      assistantText('Corrected delivery.', 'msg_2'),
      user('System: deliverable mission-report.txt: accepted.'),
      assistantText('The deliverable was accepted. Signaling completion.', 'msg_3'),
    ]);
    expect(s.items.map((i) => i.kind)).toEqual([
      'user', 'assistant', 'activity', 'assistant', 'activity', 'assistant',
    ]);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles.map((b) => b.text)).toEqual([
      'Failed delivery attempt.',
      'Corrected delivery.',
      'The deliverable was accepted. Signaling completion.',
    ]);
    // Only the newest bubble may still be pending.
    expect(bubbles.map((b) => b.pending)).toEqual([false, false, true]);
  });

  it('appends a live user echo at the end when the pending bubble is not the tail (interrupt after resume)', () => {
    // A stale pending bubble (replayed, never finalized) must not act as an
    // insertion anchor for unrelated later user events: the interrupt echo
    // arrives AFTER the resume notice and belongs at the very end.
    const s = run([
      assistantText('The deliverable was accepted.', 'msg_3'),
      user('System: you have been resumed'),
      user('[Request interrupted by user]'),
    ]);
    expect(s.items.map((i) => i.kind)).toEqual(['assistant', 'activity', 'user']);
    expect(ofKind(s, 'user')[0].text).toBe('[Request interrupted by user]');
  });

  it('renders distinct assistant messages as distinct bubbles', () => {
    // One turn can contain several assistant MESSAGES (around tool use); the
    // full-text replace dedup applies within one message, not across messages.
    const s = run([
      user('q'),
      assistantText('Let me look that up.', 'msg_a'),
      assistantText('Here is the answer.', 'msg_b'),
      result('Here is the answer.'),
    ]);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles.map((b) => b.text)).toEqual(['Let me look that up.', 'Here is the answer.']);
    expect(bubbles.map((b) => b.pending)).toEqual([false, false]);
  });

  it('still inserts the user echo before the pending bubble when it is the tail behind a tool row', () => {
    // Live streaming with an in-flight tool announcement after the pending
    // bubble: tool rows are ephemeral and do not break the "tail" notion.
    const s = run([
      delta('working on it'),
      toolUse('Bash', { command: 'ls' }),
      user('the question'),
    ]);
    const kinds = s.items.map((i) => i.kind);
    expect(kinds.indexOf('user')).toBeLessThan(kinds.indexOf('assistant'));
  });

  it('does not glue the next message\'s deltas onto the previous bubble (countdown repro)', () => {
    // Wire order per live verification: message_start announces each new
    // assistant message BEFORE its deltas; the full assistant event (same
    // message id) follows the deltas of each content block. Without
    // finalizing on message_start, message N+1's deltas append onto message
    // N's still-pending bubble: "10" became "109", "9" became "98", ...
    const s = run([
      user('count down from 10'),
      messageStart('msg_1'),
      delta('10'),
      assistantText('10', 'msg_1'),
      messageStart('msg_2'),
      delta('9'),
      assistantText('9', 'msg_2'),
      messageStart('msg_3'),
      delta('8'),
      assistantText('8', 'msg_3'),
    ]);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles.map((b) => b.text)).toEqual(['10', '9', '8']);
    expect(bubbles.map((b) => b.pending)).toEqual([false, false, true]);
  });

  it('renders a local (optimistic) user message immediately at the end', () => {
    const s = run([{ type: 'x-optio-local-user', text: 'hello there' }]);
    expect(s.items).toHaveLength(1);
    expect(s.items[0]).toMatchObject({ kind: 'user', text: 'hello there' });
    expect(s.busy).toBe(true);
  });

  it('confirms the local message in place when the wire echo arrives (no duplicate, no move)', () => {
    // The echo arrives AFTER the answer started streaming; without the local
    // match it would insert a second user bubble before the pending answer.
    const s = run([
      { type: 'x-optio-local-user', text: 'hello there' },
      delta('the answ'),
      user('hello there'),
      result('the answer'),
    ]);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant']);
    expect(ofKind(s, 'user')).toHaveLength(1);
    expect((ofKind(s, 'user')[0] as any).local).toBeUndefined();
  });

  it('confirms queued local messages FIFO by matching text', () => {
    const s = run([
      { type: 'x-optio-local-user', text: 'first' },
      { type: 'x-optio-local-user', text: 'second' },
      user('first'),
      user('second'),
    ]);
    expect(ofKind(s, 'user').map((u) => u.text)).toEqual(['first', 'second']);
    expect(ofKind(s, 'user')).toHaveLength(2);
  });

  it('appends a user message when no assistant bubble is pending (reload path)', () => {
    // On reload the buffer has no partials, so the user event arrives with no
    // pending bubble and simply appends; the result then forms the answer.
    let s = initialChatState;
    s = reduceEvent(s, user('q'), 1);
    s = reduceEvent(s, result('a'), 2);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant']);
  });
});

describe('model control fold', () => {
  const seeded = (value = ''): ChatState => ({
    ...initialChatState,
    controls: [
      {
        id: 'model',
        kind: 'select',
        label: 'Model',
        value,
        options: [{ value: 'claude-opus-4-8', label: 'Opus' }],
      },
    ],
  });

  it('folds the system/init model into an empty model control (stripping the [variant] suffix)', () => {
    const s = reduceEvent(seeded(), { type: 'system', subtype: 'init', model: 'claude-opus-4-8[1m]' }, 1);
    expect(s.controls.find((c) => c.id === 'model')!.value).toBe('claude-opus-4-8');
  });

  it('does not override a model control that already has a value (operator pick wins)', () => {
    const s = reduceEvent(
      seeded('claude-sonnet-4-6'),
      { type: 'assistant', message: { model: 'claude-opus-4-8', content: [] } },
      2,
    );
    expect(s.controls.find((c) => c.id === 'model')!.value).toBe('claude-sonnet-4-6');
  });

  it('folds an x-optio-control-update value patch onto the matching control', () => {
    const s = reduceEvent(seeded(), { type: 'x-optio-control-update', id: 'model', value: 'claude-haiku-4-5' }, 3);
    expect(s.controls.find((c) => c.id === 'model')!.value).toBe('claude-haiku-4-5');
  });

  it('is a no-op when no model control is seeded', () => {
    const s = reduceEvent(initialChatState, { type: 'system', subtype: 'init', model: 'claude-opus-4-8' }, 1);
    expect(s).toEqual(initialChatState);
  });
});

describe('System-message block separation', () => {
  it('separates multiple text blocks (coalesced System notices) with a linebreak', () => {
    // claude can echo several harness "System:" sends as ONE user event with
    // multiple text blocks; they must not render run-together.
    const ev = {
      type: 'user',
      message: {
        role: 'user',
        content: [
          { type: 'text', text: 'System: first notice' },
          { type: 'text', text: 'System: second notice' },
        ],
      },
    };
    const s = reduceEvent(initialChatState, ev, 1);
    const act = s.items.find((i) => i.kind === 'activity') as Extract<ChatItem, { kind: 'activity' }>;
    expect(act).toBeTruthy();
    expect(act.text).toBe('System: first notice\nSystem: second notice');
  });
});
