// Real Claude Code stream-json (CLI 2.1.269, claude-opus-5) through the
// production reducer. The fixtures are trimmed captures of the design's
// controlled runs (superego:/tmp/thinksum-test): uuids, session ids and usage
// blocks dropped, signatures shortened, system/init reduced, rate_limit_event
// dropped; event order, content blocks, deltas and timestamps kept.
//  - claudecode-narration-tools.jsonl: run-default-8JMk lines 1, 5-98 and
//    177-231. A text narration -> tool_use -> tool_result; a message of empty
//    thinking blocks (empty thinking_delta, signature_delta) and a call; a
//    text-bearing thinking block (narration) -> tool_use (input_json_delta)
//    -> tool_result; the final answer, result and idle.
//  - claudecode-background-task.jsonl: run-bgtask-Rr3j, whole run. A
//    backgrounded Bash (task_started, immediate tool_result), the turn end,
//    then task_updated + task_notification while idle and the follow-up turn
//    the CLI starts on its own. Only user and assistant events carry a
//    timestamp.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { initialChatState, reduceEvent } from '../claudecode/events.js';
import type { ChatItem, ChatState } from '../chat.js';
import { formatDuration } from '../duration.js';

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

describe('claudecode real wire: narration and tool rows', () => {
  const events = load('claudecode-narration-tools.jsonl');

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('narration renders as replies between persistent, finished tool rows', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tool', 'tool', 'assistant', 'tool', 'assistant']);
    const bubbles = ofKind(s, 'assistant');
    expect(bubbles[0].text).toMatch(/^Step 1: I'll run `date`/);
    expect(bubbles[1].text).toMatch(/^It's a 64-bit ARM \(aarch64\) system/);
    const resultText = events.find((e) => e.type === 'result').result;
    expect(bubbles[2].text).toBe(resultText);
    expect(bubbles.every((b) => !b.pending && !('openPart' in b))).toBe(true);
    expect(s.busy).toBe(false);
  });

  it('each call finishes with its result and wire-time duration', () => {
    const tools = ofKind(replay(events), 'tool');
    expect(tools.map((t) => [t.name, t.status, t.result, t.endedAt! - t.startedAt!])).toEqual([
      ['Bash', 'done', 'Sat Sep 12 05:45:06 AM CEST 2026', 836],
      ['Bash', 'done', 'aarch64', 263],
      ['Bash', 'done', '215', 295],
    ]);
    expect(tools.every((t) => typeof t.callId === 'string' && t.callId.startsWith('toolu_'))).toBe(true);
  });
});

describe('claudecode real wire: a background task', () => {
  const events = load('claudecode-background-task.jsonl');
  const bgRow = (s: ChatState) => ofKind(s, 'tool')[0];

  it('live (with stream_events) and replay (without) give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('a replay hours later shows the job\'s real 9 s, from task_updated end_time', () => {
    const s = replay(events);
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'tool', 'assistant', 'assistant']);
    const row = bgRow(s);
    expect(row).toMatchObject({
      name: 'Bash',
      background: true,
      taskId: 'bul7pbajt',
      status: 'done',
      result: 'Background command "Sleep 8 seconds then print a message" completed (exit code 0)',
      startedAt: Date.parse('2026-09-12T04:10:38.160Z'),
      endedAt: 1789186247223,
    });
    expect(formatDuration(row.endedAt! - row.startedAt!)).toBe('9s');
  });

  it('without task_updated, the timestamp-less notice ends the row at the latest wire time, not the reducer clock', () => {
    const s = replay(events.filter((e) => e.subtype !== 'task_updated'));
    expect(bgRow(s).endedAt).toBe(Date.parse('2026-09-12T04:10:42.198Z'));
  });

  it('busy follows session_state_changed through the follow-up turn the CLI starts on its own', () => {
    const trace: [string, boolean][] = [];
    events.reduce((s: ChatState, ev, i) => {
      if (ev.type === 'stream_event') return s;
      const next = reduceEvent(s, ev, i + 1, NOW);
      const label = ev.type === 'system' ? `system/${ev.subtype}${ev.state ? `:${ev.state}` : ''}` : ev.type;
      trace.push([label, next.busy]);
      return next;
    }, initialChatState);
    expect(trace.slice(trace.findIndex(([l]) => l === 'result'))).toEqual([
      ['result', false],
      ['system/session_state_changed:idle', false],
      ['system/background_tasks_changed', false],
      ['system/task_updated', false],
      ['system/task_notification', false],
      ['system/session_state_changed:running', true],
      ['system/init', true],
      ['system/status', true],
      ['assistant', true],
      ['result', false],
      ['system/session_state_changed:idle', false],
    ]);
  });

  it('a resume while the job ran stops its row at the latest wire time', () => {
    const cut = events.findIndex((e) => e.type === 'result');
    const s = reduceEvent(replay(events.slice(0, cut + 1)), { type: 'x-optio-resumed' }, 999, NOW);
    expect(bgRow(s)).toMatchObject({ status: 'stopped', endedAt: Date.parse('2026-09-12T04:10:42.198Z') });
  });
});
