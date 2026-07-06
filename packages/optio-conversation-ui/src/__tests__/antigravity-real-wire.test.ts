// Layer-3 (design §5, plan Task S3): replay a REAL captured antigravity
// transcript through the production reducer.
//
// The antigravity-events.test.ts suite drives the reducer with hand-written
// lines shaped like the real schema. This file replays an actual multi-turn
// transcript.jsonl captured from the real `agy -p` binary (real tool calls,
// real reasoning, the coalesced final answer) through the exact
// `reduceAntigravityEvent` the listener feeds over SSE — proving the reducer
// yields a human-correct ChatState on the real wire, not just the synthetic one.
//
// The fixture is one committed capture: two `agy -p` turns of a "reply PONG,
// then list files" conversation (12 transcript lines). To re-capture it: run a
// real Google-authed `agy` turn and copy each JSON line of
// ~/.gemini/antigravity/transcript.jsonl into the .jsonl file below.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatItem, type ChatState } from '../chat.js';
import { reduceAntigravityEvent } from '../antigravity/events.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(HERE, 'fixtures', 'antigravity-real-transcript.jsonl');

function loadFixture(): any[] {
  return fs
    .readFileSync(FIXTURE, 'utf-8')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l !== '')
    .map((l) => JSON.parse(l));
}

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceAntigravityEvent(s, ev, i), from);
}

describe('antigravity real transcript → reducer (capture-replay)', () => {
  it('reduces the real multi-turn `agy -p` capture into a human-correct ChatState', () => {
    const events = loadFixture();
    expect(events.length).toBeGreaterThan(0);

    const state = play(events);

    const users = state.items.filter(
      (i): i is Extract<ChatItem, { kind: 'user' }> => i.kind === 'user',
    );
    const answers = state.items.filter(
      (i): i is Extract<ChatItem, { kind: 'assistant' }> => i.kind === 'assistant',
    );
    const tools = state.items.filter(
      (i): i is Extract<ChatItem, { kind: 'tool' }> => i.kind === 'tool',
    );

    // Two user turns, each unwrapped to its request text (no <USER_REQUEST> tags,
    // no ADDITIONAL_METADATA / USER_SETTINGS_CHANGE noise).
    expect(users.map((u) => u.text)).toEqual([
      'Reply with exactly the word PONG, then use a tool to list files in the current directory.',
      'What single word did I ask you to reply with in my previous message?',
    ]);
    for (const u of users) {
      expect(u.text).not.toContain('<USER_REQUEST>');
      expect(u.text).not.toContain('ADDITIONAL_METADATA');
    }

    // One coalesced "PONG" answer per turn — NOT fragmented into one bubble per
    // PLANNER_RESPONSE content line (turn 1 emits "PONG" twice around its tools).
    expect(answers).toHaveLength(2);
    expect(answers.map((a) => a.text)).toEqual(['PONG', 'PONG']);
    for (const a of answers) expect(a.pending).toBe(false);

    // The real tool calls survive as durable transcript rows.
    const toolNames = tools.map((t) => t.name);
    expect(toolNames).toContain('list_dir');
    expect(toolNames).toContain('list_permissions');

    // The model's reasoning is present as a distinct reasoning row.
    expect(state.items.some((i) => i.kind === 'thinking')).toBe(true);

    // The last PLANNER_RESPONSE landed the answer → the turn ended, not busy.
    expect(state.busy).toBe(false);
  });
});
