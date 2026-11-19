// One answer text split into several text blocks of the same message.
//
// Claude Code on DeepSeek V4.1 Flash through the Requesty gateway (sales4
// analysis 6afdfcaa81727ebe47b99664, 2026-11-19): the provider interleaves its
// reasoning with the answer, so one sentence of one message arrives as
//   [thinking] [text "The mirror was"] [thinking] [text " actually down …"] [tool_use]…
// one assistant event per block, the thinking blocks unlisted (hidden
// reasoning). In that record 15 of 81 messages are split like this, every
// time mid-stream (11 between a word and a space, 2 inside a word), never at
// a line break. The model's text is the blocks joined as they are; Anthropic
// models send it as one text block. Both shapes must render the same text.

import { describe, expect, it } from 'vitest';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';

const MODEL = 'lyceum/deepseek-v4.1-flash';
// One assistant event per content block, as Claude Code emits them.
const block = (msgId: string, b: unknown) => ({ type: 'assistant', message: { role: 'assistant', id: msgId, model: MODEL, content: [b] } });
const text = (msgId: string, t: string) => block(msgId, { type: 'text', text: t });
// Hidden reasoning: a thinking block the frame does not list as narration.
const hidden = (msgId: string, t: string) => block(msgId, { type: 'thinking', thinking: t, signature: '' });
const narration = (msgId: string, t: string) => ({ ...block(msgId, { type: 'thinking', thinking: t, signature: 's' }), narration_block_indexes: [0] });
const toolUse = (msgId: string, id: string) => block(msgId, { type: 'tool_use', id, name: 'TaskCreate', input: {} });
const toolResult = (id: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: id, content: 'ok', is_error: false }] } });
const user = (t: string) => ({ type: 'user', message: { role: 'user', content: [{ type: 'text', text: t }] } });
const messageStart = (msgId: string) => ({ type: 'stream_event', event: { type: 'message_start', message: { id: msgId } } });
const textDelta = (index: number, t: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', index, delta: { type: 'text_delta', text: t } } });
const thinkingDelta = (index: number, t: string) => ({ type: 'stream_event', event: { type: 'content_block_delta', index, delta: { type: 'thinking_delta', thinking: t } } });
const result = (t: string) => ({ type: 'result', subtype: 'success', is_error: false, result: t });

const FIRST = 'The mirror was';
const SECOND = " actually down (pgrep matched its own command line). Now it's up. Let me track this new work and compare both versions across all 72 recipes.\n\n";
const JOINED = FIRST + SECOND;

function run(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, 0), from);
}
// What the listener's buffer (and so the archive) holds: never stream_event.
function replay(events: any[]): ChatState {
  return run(events.filter((ev) => ev.type !== 'stream_event'));
}
function bubbles(s: ChatState): string[] {
  return s.items.filter((i) => i.kind === 'assistant').map((i) => (i as Extract<ChatItem, { kind: 'assistant' }>).text);
}
function withoutSeq(items: ChatItem[]): unknown[] {
  return items.map(({ seq: _seq, ...rest }) => rest);
}

// The sales4 message, block for block (the reasoning texts are stand-ins).
const SPLIT_MESSAGE = [
  messageStart('m1'),
  hidden('m1', 'Reasoning about the mirror.'),
  text('m1', FIRST),
  hidden('m1', 'Okay. '),
  text('m1', SECOND),
  toolUse('m1', 't1'), toolResult('t1'),
  toolUse('m1', 't2'), toolResult('t2'),
  toolUse('m1', 't3'), toolResult('t3'),
];

describe('claudecode: one text split into several text blocks of a message', () => {
  it('replay: the text blocks join as the model wrote them, in one bubble', () => {
    const s = replay(SPLIT_MESSAGE);
    expect(bubbles(s)).toEqual([JOINED]);
    expect(s.items.map((i) => i.kind)).toEqual(['assistant', 'tool', 'tool', 'tool']);
  });

  it('live, hidden thinking streaming no text: the same text, and no blank line while the second block streams', () => {
    const head = [
      messageStart('m1'),
      thinkingDelta(0, ''), hidden('m1', ''),
      textDelta(1, 'The mirror'), textDelta(1, ' was'), text('m1', FIRST),
      thinkingDelta(2, ''), hidden('m1', ''),
      textDelta(3, ' actually'),
    ];
    expect(bubbles(run(head))).toEqual([`${FIRST} actually`]);
    const s = run([...head, textDelta(3, SECOND.slice(' actually'.length)), text('m1', SECOND), toolUse('m1', 't1')]);
    expect(bubbles(s)).toEqual([JOINED]);
  });

  it('live, hidden thinking streaming its reasoning text: the final text is the same as the replay', () => {
    const events = [
      messageStart('m1'),
      thinkingDelta(0, 'Reasoning about the mirror.'), hidden('m1', 'Reasoning about the mirror.'),
      textDelta(1, FIRST), text('m1', FIRST),
      thinkingDelta(2, 'Okay. '), hidden('m1', 'Okay. '),
      textDelta(3, SECOND), text('m1', SECOND),
      toolUse('m1', 't1'), toolResult('t1'),
      result(SECOND),
    ];
    expect(bubbles(run(events))).toEqual([JOINED]);
    expect(withoutSeq(run(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('a split inside a word joins without a space', () => {
    expect(bubbles(replay([text('m1', 'The mir'), hidden('m1', 'x'), text('m1', 'ror was up.')]))).toEqual(['The mirror was up.']);
  });

  it('line breaks the model wrote stay exactly as written', () => {
    const a = 'Plan:\n\n- one\n';
    const b = '- two\n\n```sh\nls\n```\n';
    expect(bubbles(replay([text('m1', a), hidden('m1', 'x'), text('m1', b)]))).toEqual([a + b]);
  });

  it('a text block after narration, or after a tool call, still starts a new paragraph', () => {
    expect(bubbles(replay([narration('m1', 'Checking the log.'), hidden('m1', 'x'), text('m1', 'All good.')])))
      .toEqual(['Checking the log.\n\nAll good.']);
    expect(bubbles(replay([text('m1', 'Checking the log.'), toolUse('m1', 't1'), toolResult('t1'), text('m1', 'All good.')])))
      .toEqual(['Checking the log.\n\nAll good.']);
    expect(bubbles(run([
      messageStart('m1'), textDelta(0, 'Checking the log.'), text('m1', 'Checking the log.'),
      toolUse('m1', 't1'), toolResult('t1'), textDelta(2, 'All good.'), text('m1', 'All good.'),
    ]))).toEqual(['Checking the log.\n\nAll good.']);
  });

  it('only hidden reasoning between two text blocks joins them: any other block keeps them apart', () => {
    for (const between of [{ type: 'server_tool_use', id: 's1', name: 'web_search', input: {} }, { type: 'web_search_tool_result', tool_use_id: 's1', content: [] }]) {
      expect(bubbles(replay([text('m1', 'Searching.'), block('m1', between), text('m1', 'Found it.')])))
        .toEqual(['Searching.\n\nFound it.']);
    }
  });

  it('separate messages stay separate bubbles', () => {
    expect(bubbles(replay([text('m1', 'One.'), text('m2', 'Two.')]))).toEqual(['One.', 'Two.']);
  });

  it('an answer interrupted in its second block completes the same way', () => {
    const s = run([
      user('q'),
      messageStart('m1'), textDelta(0, FIRST), text('m1', FIRST), hidden('m1', ''),
      textDelta(2, ' actually'),
      { type: 'x-optio-interrupt' },
      text('m1', ' actually'),
    ]);
    expect(bubbles(s)).toEqual([`${FIRST} actually`]);
  });

  it('an answer cut off at session end in its second block (x-optio-partial) continues the text, live and replayed', () => {
    const events = [
      user('q'),
      messageStart('m1'), textDelta(0, FIRST), text('m1', FIRST), hidden('m1', ''),
      textDelta(2, ' actually'),
      { type: 'x-optio-interrupt', by: 'session' },
      { type: 'x-optio-partial', id: 'm1', text: ' actually' },
    ];
    expect(bubbles(run(events))).toEqual([`${FIRST} actually`]);
    expect(bubbles(replay(events))).toEqual([`${FIRST} actually`]);
  });

  it('a session that ends between the blocks: live and the archive give the same items', () => {
    const events = [
      user('q'),
      messageStart('m1'), textDelta(0, FIRST), text('m1', FIRST), hidden('m1', ''),
      { type: 'x-optio-interrupt', by: 'session' },
      { type: 'x-optio-closed', reason: 'process ended' },
    ];
    // The archive holds no stream_event and no x-optio-closed (export_buffer
    // drops it); the closed row exists only live.
    const archived = run(events.filter((ev) => ev.type !== 'stream_event' && ev.type !== 'x-optio-closed'));
    const comparable = (s: ChatState) => withoutSeq(s.items.filter((i) => i.kind !== 'closed'));
    expect(comparable(run(events))).toEqual(comparable(archived));
    expect(bubbles(archived)).toEqual([FIRST]);
  });
});
