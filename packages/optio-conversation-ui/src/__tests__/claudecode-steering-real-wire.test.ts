// Real Claude Code stream-json (CLI 2.1.270, claude-sonnet-5) with optio's
// steering events, as the conversation listener buffers them. Trimmed from
// the design's synthetic runs (superego:/tmp/steer-interrupt-test) by the
// trim.py in docs/2026-09-13-conversation-steering-plan-stage1.md: hook,
// status, thinking_tokens and rate_limit events dropped; uuids, session ids,
// usage and tool callers dropped; signatures shortened; long tool outputs
// replaced by "(output trimmed)"; every message the driver sent mid-turn
// became the x-optio-queued the listener emits for it, every interrupt the
// x-optio-interrupt. Event order, content blocks, deltas and timestamps kept.
//  - claudecode-steer-streaming.jsonl (s4): a steer sent while the answer
//    streams; Claude takes it after the next tool result, same turn. This
//    recording (re-taken after a host power outage) runs two tool calls
//    (cat, then Read) before the steer is queued, one more than the run the
//    plan was written against, so replay carries two 'tool' rows there.

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

describe('claudecode real wire: a steer while the answer streams', () => {
  const events = load('claudecode-steer-streaming.jsonl');
  const paragraph = firstText(events);
  const finalText = events.find((e) => e.type === 'result').result as string;

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('the answer is one bubble, neither split nor repeated; the steer lands after the tool row it came with', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'tool', 'tool', 'assistant', 'tool', 'user', 'assistant']);
    expect(ofKind(s, 'assistant').map((b) => b.text)).toEqual([paragraph, finalText]);
    const steer = ofKind(s, 'user')[1];
    expect(steer).toMatchObject({ text: '(Steer: mention the year 1900.)', queueId: 'q1' });
    expect(steer.queued).toBeUndefined();
    expect(s.busy).toBe(false);
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
