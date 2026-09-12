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
const thinking = (text: string, msgId?: string) => ({ type: 'assistant', message: { role: 'assistant', id: msgId, content: [{ type: 'thinking', thinking: text, signature: 'sig' }] } });
const thinkingDelta = (text: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'thinking_delta', thinking: text } } });
const T0 = '2026-09-12T10:00:00.000Z';
const T5 = '2026-09-12T10:00:05.000Z';
const T9 = '2026-09-12T10:00:09.000Z';
// A reload long after the session: the reducer clock is far from the wire times.
const HOURS_LATER = Date.parse('2026-09-13T17:49:00.000Z');
const toolCall = (id: string, name: string, input: unknown, timestamp?: string) => ({ type: 'assistant', timestamp, message: { role: 'assistant', content: [{ type: 'tool_use', id, name, input }] } });
const toolResult = (id: string, content: unknown, isError = false, timestamp?: string) => ({ type: 'user', timestamp, message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content, is_error: isError }] } });
const controlRequest = (requestId: string, toolName: string, input: unknown) => ({
  type: 'control_request',
  request_id: requestId,
  request: { subtype: 'can_use_tool', tool_name: toolName, input },
});

const taskStarted = (taskId: string, toolUseId: string) => ({ type: 'system', subtype: 'task_started', task_id: taskId, tool_use_id: toolUseId, description: 'bg job', is_backgrounded: true, task_type: 'local_bash' });
// System and result events carry no timestamp on the wire (CLI 2.1.269); only
// user and assistant events do.
const taskNotification = (taskId: string, toolUseId: string, status: string, summary: string) => ({ type: 'system', subtype: 'task_notification', task_id: taskId, tool_use_id: toolUseId, status, output_file: '/tmp/x.output', summary });
const taskUpdated = (taskId: string, endTime: number) => ({ type: 'system', subtype: 'task_updated', task_id: taskId, patch: { status: 'completed', end_time: endTime } });
const injectedNotification = (taskId: string, toolUseId: string, status: string, summary: string) => ({
  type: 'user',
  origin: { kind: 'task-notification' },
  message: {
    role: 'user',
    content: `<task-notification>\n<task-id>${taskId}</task-id>\n<tool-use-id>${toolUseId}</tool-use-id>\n<output-file>/tmp/x.output</output-file>\n<status>${status}</status>\n<summary>${summary}</summary>\n</task-notification>`,
  },
});

function run(events: any[], from: ChatState = initialChatState, now?: number): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, now), from);
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
    name: 'a new tool call adds a row; earlier rows stay',
    events: [toolUse('ToolSearch', { query: 'x' }), toolUse('WebSearch', { query: 'y' })],
    check: (s) => {
      expect(ofKind(s, 'tool').map((t) => t.name)).toEqual(['ToolSearch', 'WebSearch']);
    },
  },
  {
    name: 'a permission request keeps the earlier tool row',
    events: [
      toolUse('WebSearch', { query: 'y' }),
      controlRequest('perm-1', 'WebSearch', { query: 'y' }),
    ],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(1);
      expect(ofKind(s, 'permission')).toHaveLength(1);
    },
  },
  {
    name: 'an assistant answer keeps the tool row above it',
    events: [toolUse('Read', { file_path: '/x' }), assistantText('here is the answer')],
    check: (s) => {
      expect(s.items.map((i) => i.kind)).toEqual(['tool', 'assistant']);
    },
  },
  {
    name: 'a trailing tool use stays, frozen, at session close',
    events: [toolUse('Bash', { command: 'echo DONE >> ./optio.log' }), { type: 'x-optio-closed', reason: 'process ended' }],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(1);
      expect(ofKind(s, 'tool')[0].endedAt).toBeTypeOf('number');
      expect(ofKind(s, 'closed')).toHaveLength(1);
    },
  },
  {
    name: 'result keeps a running tool row and freezes it',
    events: [toolUse('Bash', { command: 'x' }), result('done')],
    check: (s) => {
      expect(ofKind(s, 'tool')).toHaveLength(1);
      expect(ofKind(s, 'tool')[0].endedAt).toBeTypeOf('number');
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

describe('real tool rows', () => {
  it('tool rows persist across later tool calls and replies', () => {
    const s = run([toolCall('t1', 'Bash', { command: 'ls' }), toolCall('t2', 'Read', { file_path: '/x' }), assistantText('done reading')]);
    expect(ofKind(s, 'tool').map((t) => t.name)).toEqual(['Bash', 'Read']);
    expect(ofKind(s, 'tool').map((t) => t.callId)).toEqual(['t1', 't2']);
    expect(ofKind(s, 'assistant')).toHaveLength(1);
  });

  it('a tool_result finishes its row with status, result and endedAt', () => {
    const s = run([toolCall('t1', 'Bash', { command: 'ls' }, T0), toolResult('t1', 'file1\nfile2\n', false, T5)]);
    const [row] = ofKind(s, 'tool');
    expect(row).toMatchObject({ status: 'done', result: 'file1\nfile2', startedAt: Date.parse(T0), endedAt: Date.parse(T5) });
    expect(ofKind(s, 'user')).toEqual([]);
  });

  it('an error tool_result marks the row failed', () => {
    const s = run([toolCall('t1', 'Bash', { command: 'false' }), toolResult('t1', 'exit 1', true)]);
    expect(ofKind(s, 'tool')[0].status).toBe('failed');
  });

  it('block-list result content contributes its text blocks', () => {
    const s = run([toolCall('t1', 'Read', {}), toolResult('t1', [{ type: 'text', text: 'a' }, { type: 'image' }, { type: 'text', text: 'b' }])]);
    expect(ofKind(s, 'tool')[0].result).toBe('a\nb');
  });

  it('long results are trimmed to 2000 characters', () => {
    const s = run([toolCall('t1', 'Bash', {}), toolResult('t1', 'x'.repeat(5000))]);
    const r = ofKind(s, 'tool')[0].result!;
    expect(r).toHaveLength(2000);
    expect(r.endsWith('…')).toBe(true);
  });

  it('a tool_result for an unknown id changes nothing', () => {
    const before = run([toolCall('t1', 'Bash', {})]);
    const after = reduceEvent(before, toolResult('nope', 'x'), 9);
    expect(after.items).toEqual(before.items);
  });

  it('startedAt falls back to the reducer clock when the event has no timestamp', () => {
    const s = reduceEvent(initialChatState, toolCall('t1', 'Bash', {}), 1, 12_345);
    expect(ofKind(s, 'tool')[0].startedAt).toBe(12_345);
  });

  it('result freezes a running row at the latest wire time and leaves finished rows alone', () => {
    const s = run(
      [
        toolCall('t1', 'Bash', {}, T0),
        toolResult('t1', 'ok', false, T5),
        toolCall('t2', 'Bash', {}, T5),
        { ...assistantText('still waiting'), timestamp: T9 },
        result('done'),
      ],
      initialChatState,
      HOURS_LATER,
    );
    const [a, b] = ofKind(s, 'tool');
    expect(a.endedAt).toBe(Date.parse(T5));
    expect(b.endedAt).toBe(Date.parse(T9));
    expect(b.status).toBeUndefined();
  });

  it('before any wire time has been seen, the reducer clock is the fallback', () => {
    const s = run([toolUse('Bash', {}), result('x')], initialChatState, 4242);
    expect(ofKind(s, 'tool')[0].endedAt).toBe(4242);
  });

  it('lastEventAt keeps the latest user/assistant timestamp and ignores other events', () => {
    const s = run([
      toolCall('t1', 'Bash', {}, T5),
      toolResult('t1', 'ok', false, T0),
      { type: 'system', subtype: 'status', timestamp: '2030-01-01T00:00:00.000Z' },
    ]);
    expect(s.lastEventAt).toBe(Date.parse(T5));
  });
});

describe('narration from thinking blocks', () => {
  // Since CLI 2.1.267 the model writes its between-tool narration as a
  // text-bearing thinking block; the real reasoning is an empty thinking block.

  it('a text-bearing thinking block becomes an assistant bubble', () => {
    const s = run([user('q'), thinking("I'll check the VPN first.", 'm1')]);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles.map((b) => b.text)).toEqual(["I'll check the VPN first."]);
    expect(bubbles[0].pending).toBe(true);
  });

  it('an empty thinking block (hidden reasoning) is ignored', () => {
    const s = run([thinking('', 'm1'), thinking('   ', 'm1')]);
    expect(s.items).toEqual([]);
  });

  it('thinking_delta streams narration and the final thinking event does not duplicate it', () => {
    const mid = run([messageStart('m1'), thinkingDelta("I'll "), thinkingDelta('check.')]);
    expect(ofKind(mid, 'assistant').map((b) => b.text)).toEqual(["I'll check."]);
    const s = reduceEvent(mid, thinking("I'll check.", 'm1'), 4);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual(["I'll check."]);
  });

  it('narration and text in one message form one bubble with two parts (live)', () => {
    const s = run([
      messageStart('m1'),
      thinking('', 'm1'),
      thinkingDelta('Checking the log.'),
      thinking('Checking the log.', 'm1'),
      delta('All good.'),
      assistantText('All good.', 'm1'),
    ]);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual(['Checking the log.\n\nAll good.']);
  });

  it('narration and text in one message form one bubble with two parts (replay, no deltas)', () => {
    const s = run([thinking('Checking the log.', 'm1'), assistantText('All good.', 'm1')]);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual(['Checking the log.\n\nAll good.']);
  });

  it('result keeps narration that precedes the result text in the same message', () => {
    const s = run([user('q'), thinking('Checking the log.', 'm1'), assistantText('All good.', 'm1'), result('All good.')]);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles.map((b) => b.text)).toEqual(['Checking the log.\n\nAll good.']);
    expect(bubbles[0].pending).toBe(false);
  });

  it('narration in different messages stays in separate bubbles', () => {
    const s = run([thinking('Step one.', 'm1'), thinking('Step two.', 'm2')]);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual(['Step one.', 'Step two.']);
  });

  it('finalized bubbles carry no openPart bookkeeping', () => {
    const s = run([messageStart('m1'), delta('10'), messageStart('m2'), delta('9'), result('9')]);
    for (const b of ofKind(s, 'assistant')) expect('openPart' in b).toBe(false);
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

describe('claudecode upload notice → attachment row', () => {
  it('splits an upload notice into a clean bubble + attachment row, deduping the optimistic echo (live path)', () => {
    const s = run([
      { type: 'x-optio-local-user', text: 'review the screenshot' },
      user('System: upload received, stored in uploads/pic.png\n\nreview the screenshot'),
    ]);
    const users = s.items.filter((i) => i.kind === 'user');
    expect(users).toHaveLength(1);
    expect(users[0].kind === 'user' && users[0].text).toBe('review the screenshot');
    expect(users[0].kind === 'user' && users[0].local).toBeFalsy();
    const attach = s.items.find((i) => i.kind === 'activity');
    expect(attach && attach.kind === 'activity' && attach.text).toBe('📎 Attached: pic.png');
    expect(s.items.findIndex((i) => i.kind === 'activity')).toBeLessThan(
      s.items.findIndex((i) => i.kind === 'user'));
  });

  it('replays the attachment row from a resumed user echo — even after the answer streamed first (the resume guarantee)', () => {
    // --replay-user-messages streams the answer BEFORE echoing the user turn,
    // so the attachment row + bubble slot in front of the pending assistant.
    const s = run([
      delta('here is the review'),
      user('System: upload received, stored in uploads/spec.md\n\nimplement it'),
    ]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('implement it');
    const attach = s.items.find((i) => i.kind === 'activity');
    expect(attach && attach.kind === 'activity' && attach.text).toBe('📎 Attached: spec.md');
    // Chronological order: attachment row, then user bubble, then the answer.
    const ai = s.items.findIndex((i) => i.kind === 'activity');
    const ui = s.items.findIndex((i) => i.kind === 'user');
    const asi = s.items.findIndex((i) => i.kind === 'assistant');
    expect(ai).toBeLessThan(ui);
    expect(ui).toBeLessThan(asi);
  });

  it('surfaces an x-optio-local-error as an error row', () => {
    const s = run([{ type: 'x-optio-local-error', text: 'Upload failed: big.png — exceeds the size limit' }]);
    const e = s.items.find((i) => i.kind === 'error');
    expect(e && e.kind === 'error' && e.text).toBe('Upload failed: big.png — exceeds the size limit');
  });
});

describe('background tasks', () => {
  const started = [
    toolCall('t1', 'Bash', { command: './harness.sh --lean' }, T0),
    taskStarted('b1', 't1'),
    toolResult('t1', 'Command running in background with ID: b1', false, T5),
  ];

  it('a backgrounded call stays running after its immediate tool_result', () => {
    const [row] = ofKind(run(started), 'tool');
    expect(row.background).toBe(true);
    expect(row.status).toBe('running');
    expect(row.endedAt).toBeUndefined();
  });

  it('task_started records the task id on the row', () => {
    expect(ofKind(run(started), 'tool')[0].taskId).toBe('b1');
  });

  it('task_updated end_time ends the row, and the notification keeps that time', () => {
    const end = Date.parse('2026-09-12T10:06:12.000Z');
    const s = run(
      [...started, taskUpdated('b1', end), taskNotification('b1', 't1', 'completed', 'Lean-verify all 15')],
      initialChatState,
      HOURS_LATER,
    );
    const [row] = ofKind(s, 'tool');
    expect(row).toMatchObject({ status: 'done', result: 'Lean-verify all 15', startedAt: Date.parse(T0), endedAt: end });
  });

  it('without task_updated, a notification ends the row at the latest wire time, not the reducer clock', () => {
    const s = run([...started, taskNotification('b1', 't1', 'completed', 'Lean-verify all 15')], initialChatState, HOURS_LATER);
    expect(ofKind(s, 'tool')[0].endedAt).toBe(Date.parse(T5));
  });

  it('task_updated without an end_time, or for an unknown task, changes nothing', () => {
    const base = run(started);
    const noEnd = { type: 'system', subtype: 'task_updated', task_id: 'b1', patch: { status: 'running' } };
    expect(reduceEvent(base, noEnd, 9).items).toBe(base.items);
    expect(reduceEvent(base, taskUpdated('nope', 1), 9).items).toBe(base.items);
  });

  it('a replay hours later shows the real duration of a background job', () => {
    const events = [
      ...started,
      result('started it'),
      taskUpdated('b1', Date.parse(T0) + 9_063),
      taskNotification('b1', 't1', 'completed', 'done'),
    ];
    const [row] = ofKind(run(events, initialChatState, HOURS_LATER), 'tool');
    expect(row.endedAt! - row.startedAt!).toBe(9_063);
  });

  it('a failed task marks the row failed', () => {
    const s = run([...started, taskNotification('b1', 't1', 'failed', 'Build test venv')]);
    expect(ofKind(s, 'tool')[0].status).toBe('failed');
  });

  it('an injected <task-notification> turn finishes the row and adds no user bubble', () => {
    const s = run([...started, injectedNotification('b1', 't1', 'completed', 'Rewrite harness')]);
    expect(ofKind(s, 'user')).toEqual([]);
    expect(ofKind(s, 'tool')[0]).toMatchObject({ status: 'done', result: 'Rewrite harness' });
  });

  it('the same task reported by both routes applies once', () => {
    const s = run([
      ...started,
      taskNotification('b1', 't1', 'completed', 'first report'),
      injectedNotification('b1', 't1', 'completed', 'second report'),
    ]);
    expect(ofKind(s, 'tool')[0].result).toBe('first report');
    expect(ofKind(s, 'activity')).toEqual([]);
    expect(s.finishedTaskIds).toEqual(['b1']);
  });

  it('without a matching row, a muted activity row reports the finished task', () => {
    const ok = run([taskNotification('b9', 't9', 'completed', 'Nightly export')]);
    expect(ofKind(ok, 'activity').map((a) => a.text)).toEqual(['✓ Background task finished: Nightly export']);
    const bad = run([injectedNotification('b8', 't8', 'failed', 'Nightly import')]);
    expect(ofKind(bad, 'activity').map((a) => a.text)).toEqual(['✗ Background task failed: Nightly import']);
  });

  it('the end of the turn does not freeze a background row; session close does', () => {
    const mid = run([...started, result('started it')]);
    expect(ofKind(mid, 'tool')[0].endedAt).toBeUndefined();
    const closed = reduceEvent(mid, { type: 'x-optio-closed', reason: 'stopped' }, 99, HOURS_LATER);
    expect(ofKind(closed, 'tool')[0].endedAt).toBe(Date.parse(T5));
  });

  it('task_started arriving after the tool_result reopens the row', () => {
    const s = run([
      toolCall('t1', 'Bash', {}, T0),
      toolResult('t1', 'Command running in background with ID: b1', false, T5),
      taskStarted('b1', 't1'),
    ]);
    const [row] = ofKind(s, 'tool');
    expect(row).toMatchObject({ background: true, status: 'running' });
    expect(row.endedAt).toBeUndefined();
    expect(row.result).toBeUndefined();
  });

  it('other system events are still ignored', () => {
    const s = run([{ type: 'system', subtype: 'status' }, { type: 'system', subtype: 'thinking_tokens' }]);
    expect(s).toEqual(initialChatState);
  });

  it('an injected notification marks the agent busy for the CLI-follow-up model turn', () => {
    const s = run([...started, result('started it'), injectedNotification('b1', 't1', 'completed', 'Rewrite harness')]);
    expect(s.busy).toBe(true);
  });

  it('a result following an injected notification clears busy again', () => {
    const s = run([
      ...started,
      result('started it'),
      injectedNotification('b1', 't1', 'completed', 'Rewrite harness'),
      result('final answer'),
    ]);
    expect(s.busy).toBe(false);
  });

  it('system/task_notification leaves busy unchanged, whether true or false', () => {
    const wasBusy = run(started, { ...initialChatState, busy: true });
    expect(wasBusy.busy).toBe(true);
    const stillBusy = reduceEvent(wasBusy, taskNotification('b1', 't1', 'completed', 'done'), 4, 1234);
    expect(stillBusy.busy).toBe(true);

    const wasIdle = run(started, { ...initialChatState, busy: false });
    expect(wasIdle.busy).toBe(false);
    const stillIdle = reduceEvent(wasIdle, taskNotification('b1', 't1', 'completed', 'done'), 4, 1234);
    expect(stillIdle.busy).toBe(false);
  });

  it('a user event whose origin is not task-notification renders normally even if its text starts with <task-notification>', () => {
    const genuine = {
      type: 'user',
      origin: { kind: 'user' },
      message: { role: 'user', content: [{ type: 'text', text: '<task-notification> what does this mean?' }] },
    };
    const s = run([...started, genuine]);
    expect(ofKind(s, 'user')).toEqual([{ kind: 'user', text: '<task-notification> what does this mean?', seq: 4 }]);
    expect(ofKind(s, 'tool')[0]).toMatchObject({ status: 'running' });
  });

  it('an injected notification with no origin (an older replay) is still consumed', () => {
    const legacy = {
      type: 'user',
      message: {
        role: 'user',
        content:
          '<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>t1</tool-use-id>\n<status>completed</status>\n<summary>legacy replay</summary>\n</task-notification>',
      },
    };
    const s = run([...started, legacy]);
    expect(ofKind(s, 'user')).toEqual([]);
    expect(ofKind(s, 'tool')[0]).toMatchObject({ status: 'done', result: 'legacy replay' });
  });

  it('a "stopped" task_notification marks the row stopped, not done or failed', () => {
    const s = run([...started, taskNotification('b1', 't1', 'stopped', 'Killed by operator')]);
    expect(ofKind(s, 'tool')[0]).toMatchObject({ status: 'stopped', result: 'Killed by operator' });
  });

  it('a "stopped" outcome with no matching row reports a muted activity line', () => {
    const s = run([taskNotification('b9', 't9', 'killed', 'Nightly export')]);
    expect(ofKind(s, 'activity').map((a) => a.text)).toEqual(['⏹ Background task stopped: Nightly export']);
  });

  it('the same "stopped" outcome through the injected-turn route matches', () => {
    const withRow = run([...started, injectedNotification('b1', 't1', 'stopped', 'Killed by operator')]);
    expect(ofKind(withRow, 'tool')[0]).toMatchObject({ status: 'stopped', result: 'Killed by operator' });
    const noRow = run([injectedNotification('b9', 't9', 'killed', 'Nightly export')]);
    expect(ofKind(noRow, 'activity').map((a) => a.text)).toEqual(['⏹ Background task stopped: Nightly export']);
  });

  it('a late task_started arriving after the task already finished does not reopen the row', () => {
    const s = run([...started, taskNotification('b1', 't1', 'completed', 'already done'), taskStarted('b1', 't1')]);
    const [row] = ofKind(s, 'tool');
    expect(row).toMatchObject({ status: 'done', result: 'already done' });
    expect(row.endedAt).toBeDefined();
  });

  it('a summary over 2000 characters is trimmed to exactly 2000 characters ending in an ellipsis', () => {
    const long = 'x'.repeat(2500);
    const s = run([...started, taskNotification('b1', 't1', 'completed', long)]);
    const result = ofKind(s, 'tool')[0].result!;
    expect(result).toHaveLength(2000);
    expect(result.endsWith('…')).toBe(true);
  });

  it('a malformed <task-notification> with no <task-id> leaves state unchanged and adds no bubble', () => {
    const malformed = {
      type: 'user',
      origin: { kind: 'task-notification' },
      message: {
        role: 'user',
        content: '<task-notification>\n<status>completed</status>\n<summary>oops</summary>\n</task-notification>',
      },
    };
    const s = run([malformed]);
    expect(s).toEqual(initialChatState);
  });
});
