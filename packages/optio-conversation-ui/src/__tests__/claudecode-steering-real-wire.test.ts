// Real Claude Code stream-json (CLI 2.1.270, claude-sonnet-5) with optio's
// steering events, as the conversation listener buffers them. Trimmed from
// clean recordings (superego:/tmp/steer-interrupt-test, taken with
// --setting-sources project,local so no host-specific ~/.claude/CLAUDE.md
// or its extra tool calls leak in) by the trim.py in
// docs/2026-09-13-conversation-steering-plan-stage1.md: hook, status,
// thinking_tokens and rate_limit events dropped; uuids, session ids, usage
// and tool callers dropped; signatures shortened; long tool outputs
// replaced by "(output trimmed)"; every message the driver sent mid-turn
// became the x-optio-queued the listener emits for it, every interrupt the
// x-optio-interrupt. Event order, content blocks, deltas and timestamps kept.
//  - claudecode-steer-streaming.jsonl (s4): a steer sent while the answer
//    streams, before any tool call. Claude takes it right after the
//    `echo step2` tool result that follows the paragraph, same turn: its
//    echo is isReplay with an earlier timestamp than that tool_result's.
//  - claudecode-two-steers-streaming.jsonl (s5): two steers sent while the
//    answer streams. Claude finishes that turn, then takes both together at
//    the start of one follow-up turn: ONE isReplay echo whose
//    message.content holds two text blocks, one per queued message, each
//    the exact sent text (CLI 2.1.270).
//  - claudecode-two-steers-tool.jsonl (s6): two steers sent while a
//    (backgrounded) tool call runs. Claude takes them at the next tool
//    result, same turn, as two separate one-block isReplay echoes joined
//    into the running turn.

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

// A reload hours after the runs: the reducer clock is far from the wire times.
const NOW = Date.parse('2026-09-13T17:49:00.000Z');

// Live: every event, as the SSE live tail delivers it. Replay: what the
// listener's buffer holds (never stream_event), with the original seqs.
function live(events: any[], now = NOW): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, now), initialChatState);
}
function replay(events: any[], now = NOW): ChatState {
  return events.reduce((s, ev, i) => (ev.type === 'stream_event' ? s : reduceEvent(s, ev, i + 1, now)), initialChatState);
}
// seq is a React key and differs by construction (live, a bubble takes the
// seq of its first delta; replayed, that of its assistant event).
function withoutSeq(items: ChatItem[]): unknown[] {
  return items.map(({ seq: _seq, ...rest }) => rest);
}
function ofKind<K extends ChatItem['kind']>(state: ChatState, kind: K): Extract<ChatItem, { kind: K }>[] {
  return state.items.filter((i) => i.kind === kind) as Extract<ChatItem, { kind: K }>[];
}
const firstText = (events: any[]) =>
  events.find((e) => e.type === 'assistant' && e.message.content[0]?.type === 'text').message.content[0].text as string;
// The recording's LAST result (s5/s6 run two turns, so two 'result' events).
const lastResultText = (events: any[]) => {
  const results = events.filter((e) => e.type === 'result');
  return results[results.length - 1].result as string;
};
// True once nothing on screen is still a queued bubble, and no undelivered
// note was left behind for one (dropUndelivered only fires on session end,
// which none of these recordings reach, but a bubble left queued forever
// would still be a bug this guards against).
function noneLeftQueued(state: ChatState): void {
  expect(ofKind(state, 'user').some((u) => u.queued)).toBe(false);
  expect(ofKind(state, 'activity').some((a) => a.text.startsWith('Not delivered'))).toBe(false);
}

describe('claudecode real wire: a steer while the answer streams', () => {
  const events = load('claudecode-steer-streaming.jsonl');
  const paragraph = firstText(events);
  const finalText = lastResultText(events);

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('the answer is one bubble, neither split nor repeated; the steer lands after the tool row it came with', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tool', 'user', 'assistant']);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual([paragraph, finalText]);
    const steer = ofKind(s, 'user')[1];
    expect(steer).toMatchObject({ text: '(Steer: mention the year 1900.)', queueId: 'q1' });
    expect(steer.queued).toBeUndefined();
    expect(s.busy).toBe(false);
    noneLeftQueued(s);
  });

  it('while the steer waits it is the last item, below one pending answer', () => {
    const cut = events.findIndex((e) => e.type === 'assistant' && e.message.content[0]?.type === 'text');
    const s = live(events.slice(0, cut));
    expect(s.items[s.items.length - 1]).toMatchObject({ kind: 'user', queued: true, queueId: 'q1' });
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].pending).toBe(true);
    expect(paragraph.startsWith(bubbles[0].text)).toBe(true);
  });
});

describe('claudecode real wire: two steers queued while the answer streams (one joint echo)', () => {
  const events = load('claudecode-two-steers-streaming.jsonl');
  const firstAnswer = firstText(events);
  const secondAnswer = lastResultText(events);

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('both queued bubbles leave the queued state, in order, between the two answers; none left queued', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'user', 'user', 'assistant']);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual([firstAnswer, secondAnswer]);
    const [steerA, steerB] = ofKind(s, 'user').slice(1);
    expect(steerA).toMatchObject({ text: '(Steer A: mention the colour blue.)', queueId: 'q1' });
    expect(steerB).toMatchObject({ text: '(Steer B: mention the number seven.)', queueId: 'q2' });
    expect(steerA.queued).toBeUndefined();
    expect(steerB.queued).toBeUndefined();
    expect(s.busy).toBe(false);
    noneLeftQueued(s);
  });

  it('while both wait they are pinned at the bottom, in order, below one pending answer', () => {
    const cut = events.findIndex((e) => e.type === 'assistant' && e.message.content[0]?.type === 'text');
    const s = live(events.slice(0, cut));
    expect(s.items.slice(-2)).toMatchObject([
      { kind: 'user', queued: true, queueId: 'q1' },
      { kind: 'user', queued: true, queueId: 'q2' },
    ]);
  });
});

describe('claudecode real wire: two steers queued during a tool call (two separate echoes)', () => {
  const events = load('claudecode-two-steers-tool.jsonl');
  const finalText = lastResultText(events);

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('both queued bubbles leave the queued state, in order, after the tool row they came with; none left queued', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'tool', 'user', 'user', 'assistant']);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual([finalText]);
    const [steerA, steerB] = ofKind(s, 'user').slice(1);
    expect(steerA).toMatchObject({ text: "(Steer A: also tell me today's date.)", queueId: 'q1' });
    expect(steerB).toMatchObject({ text: '(Steer B: also say hello.)', queueId: 'q2' });
    expect(steerA.queued).toBeUndefined();
    expect(steerB.queued).toBeUndefined();
    expect(s.busy).toBe(false);
    noneLeftQueued(s);
  });

  it('while both wait they are pinned at the bottom, in order, below the running tool row', () => {
    const cut = events.findIndex((e) => e.type === 'user' && e.message.content[0]?.type === 'tool_result');
    const s = live(events.slice(0, cut));
    expect(s.items.slice(-2)).toMatchObject([
      { kind: 'user', queued: true, queueId: 'q1' },
      { kind: 'user', queued: true, queueId: 'q2' },
    ]);
  });
});
