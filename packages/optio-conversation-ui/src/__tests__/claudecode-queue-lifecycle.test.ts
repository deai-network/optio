// Real Claude Code stream-json (CLI 2.1.270) with the CLI's OWN queue
// lifecycle (command_lifecycle, uuid'd stdin messages, isReplay echoes
// carrying their uuid) -- Fix 13b, owner ruling 2026-09-15: "the UI moves
// queued bubbles by the CLI's command_lifecycle events (by uuid) instead of
// matching echo text". See cli-queue-lifecycle.md for the event shapes and
// orderings this fix implements.
//
// Trimmed (trim_lifecycle.py, a Fix-13b variant of the fix-5 trim.py: also
// keeps command_lifecycle and each user/assistant event's own uuid, and
// synthesizes x-optio-requeued for a cancel+interrupt+resend instead of a
// second x-optio-queued) from the raw driver recordings
// excavator:~/deai/optio-steering/.superpowers/recordings/prod/q-*.jsonl,
// which drove the CLI directly (no optio server in the loop) to observe its
// native queue -- these recordings carry no x-optio-* events of their own.
//   - claudecode-queue-idle.jsonl (q-idle): three idle sends in a row. The
//     second ("Say OK.") shows command_lifecycle 'queued' and 'started'
//     arriving back-to-back, for a plain (never queued) send.
//   - claudecode-queue-tool.jsonl (q-tool): two messages sent while a
//     backgrounded Bash call runs, taken at its tool_result -- one with a
//     uuid (lifecycle-driven), one without (legacy text-matching fallback),
//     side by side.
//   - claudecode-queue-fold.jsonl (q-fold): three messages queued while an
//     essay streams, folded into one turn after it ends: two solo echoes
//     (no timestamp) arrive before their own 'started', the third only
//     'started', then one combined echo (all 3 texts, the last uuid, a
//     timestamp) confirms it and must add no duplicates for the other two.
//   - claudecode-queue-cancel3.jsonl (q-cancel3): three queued, "Send now"
//     on the second cancels the third, interrupts, delivers 1-2, then
//     re-sends 3 under a new uuid once 2's own 'started' is seen --
//     x-optio-requeued (synthesized at the resend, per steering.py's
//     _send_now_up_to, which never emits a second x-optio-queued for it)
//     re-keys the bubble the CLI's own 'cancelled' had already turned into
//     a "Not delivered" note.

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

const NOW = Date.parse('2026-09-15T05:00:00.000Z');
// Live: every event, as the SSE live tail delivers it. Replay: what the
// listener's buffer holds (command_lifecycle, control_response and user
// echoes are top-level types, not stream_event, so they ARE buffered --
// cli-queue-lifecycle.md §5).
function live(events: any[], now = NOW): ChatState {
  return events.reduce((s, ev, i) => reduceEvent(s, ev, i + 1, now), initialChatState);
}
function replay(events: any[], now = NOW): ChatState {
  return events.reduce((s, ev, i) => (ev.type === 'stream_event' ? s : reduceEvent(s, ev, i + 1, now)), initialChatState);
}
function withoutSeq(items: ChatItem[]): unknown[] {
  return items.map(({ seq: _seq, ...rest }) => rest);
}
function ofKind<K extends ChatItem['kind']>(state: ChatState, kind: K): Extract<ChatItem, { kind: K }>[] {
  return state.items.filter((i) => i.kind === kind) as Extract<ChatItem, { kind: K }>[];
}
function noneLeftQueued(state: ChatState): void {
  expect(ofKind(state, 'user').some((u) => u.queued)).toBe(false);
  expect(ofKind(state, 'activity').some((a) => a.text.startsWith('Not delivered'))).toBe(false);
}
function texts(state: ChatState): string[] {
  return ofKind(state, 'user').map((u) => u.text);
}

describe('claudecode real wire: idle send with queued+started back to back', () => {
  const events = load('claudecode-queue-idle.jsonl');

  it('live and replay give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('every send resolves to one plain (non-queued) bubble, none left queued or undelivered', () => {
    const s = replay(events);
    noneLeftQueued(s);
    expect(texts(s)).toEqual(['Say hello in one short sentence. Do not use any tools.', 'Say OK.', 'Say OK again.']);
    expect(s.busy).toBe(false);
  });
});

describe('claudecode real wire: a uuid-driven and a legacy message taken together at a tool boundary', () => {
  const events = load('claudecode-queue-tool.jsonl');

  it('live and replay give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('both resolve, in order, right after the tool row; neither stays queued', () => {
    const s = replay(events);
    noneLeftQueued(s);
    const kinds = s.items.map((i) => i.kind);
    const toolIdx = kinds.indexOf('tool');
    expect(toolIdx).toBeGreaterThan(-1);
    const after = s.items.slice(toolIdx + 1).filter((i): i is Extract<ChatItem, { kind: 'user' }> => i.kind === 'user');
    expect(after.map((u) => u.text)).toEqual([
      'Queued message ONE: include the word apple in your reply.',
      'Queued message TWO (no uuid): include the word banana in your reply.',
    ]);
  });
});

describe('claudecode real wire: a fold of 3 (two solo echoes, started x3, one combined echo)', () => {
  const events = load('claudecode-queue-fold.jsonl');

  it('live and replay give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('all three resolve exactly once, none left queued, the last gets the combined echo\'s timestamp', () => {
    const s = replay(events);
    noneLeftQueued(s);
    const queuedTexts = [
      'Queued message ONE: include the word apple in your reply.',
      'Queued message TWO: include the word banana in your reply.',
      'Queued message THREE: include the word cherry in your reply.',
    ];
    for (const t of queuedTexts) {
      expect(texts(s).filter((x) => x === t)).toEqual([t]);
    }
    const last = ofKind(s, 'user').find((u) => u.text === queuedTexts[2]);
    expect(last?.timestamp).toBe(Date.parse('2026-09-14T22:12:39.498Z'));
  });
});

describe('claudecode real wire: cancel3 (Send now on message 2 cancels+resends message 3)', () => {
  const events = load('claudecode-queue-cancel3.jsonl');
  const texts3 = [
    'Queued message ONE: include the word apple in your reply.',
    'Queued message TWO: include the word banana in your reply.',
    'Queued message THREE: include the word cherry in your reply.',
  ];

  it('live and replay give the same items', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
  });

  it('every message is eventually delivered exactly once, none left queued or Not delivered', () => {
    const s = replay(events);
    noneLeftQueued(s);
    for (const t of texts3) {
      expect(texts(s).filter((x) => x === t)).toEqual([t]);
    }
  });

  it('message 3 is confirmed under its re-keyed uuid, with the final echo\'s own timestamp', () => {
    const s = replay(events);
    const third = ofKind(s, 'user').find((u) => u.text === texts3[2]);
    expect(third?.queueId).toBe('ff3eef32-5f82-4640-a951-1044e839583e');
    expect(third?.timestamp).toBe(Date.parse('2026-09-14T21:58:34.209Z'));
  });

  it('at no point does a "Not delivered" note survive to the end (the cancel that belongs to our own requeue is ignored)', () => {
    const s = replay(events);
    expect(s.items.some((i) => i.kind === 'activity' && i.text.startsWith('Not delivered'))).toBe(false);
  });
});

describe('claudecode real wire: a legacy fixture with no uuids still resolves by text matching (fallback, Fix 13b)', () => {
  // Predates Fix 13a: no `uuid` on any stdin message, so the CLI never
  // emits a single command_lifecycle for this conversation. Regression
  // coverage that the uuid-matching added by Fix 13b does not disturb it.
  const events = load('claudecode-two-steers-streaming.jsonl');

  it('live and replay still agree, and nothing is left queued', () => {
    expect(withoutSeq(live(events).items)).toEqual(withoutSeq(replay(events).items));
    noneLeftQueued(replay(events));
  });
});
