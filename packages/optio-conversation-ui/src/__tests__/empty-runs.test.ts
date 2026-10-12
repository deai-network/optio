import { describe, it, expect } from 'vitest';
import { withoutEmptyRuns } from '../runs.js';
import type { ChatItem } from '../chat.js';

// hideEmptyRuns (owner request 2026-10-12): the runs a resume added where
// nothing happened (the resume band, the harness resume notice and the
// agent's "ready" reply) are left out; a run where something happened, the
// first run, and (live) the current last run stay.

let seq = 0;
const band = (): ChatItem => ({ kind: 'resumed', seq: ++seq });
const notice = (): ChatItem => ({ kind: 'activity', text: 'System: you have been resumed', seq: ++seq, system: true });
const reply = (text: string): ChatItem => ({ kind: 'assistant', text, pending: false, seq: ++seq, msgId: null });
const ask = (text: string): ChatItem => ({ kind: 'user', text, seq: ++seq });
// An empty resumed run: band, notice, "ready" reply.
const emptyRun = (): ChatItem[] => [band(), notice(), reply("OK, I'm ready.")];

const texts = (items: ChatItem[]) => items.map((i) => ('text' in i ? `${i.kind}:${i.text}` : i.kind));

describe('withoutEmptyRuns', () => {
  it('leaves a transcript with no resume as it is (the same array)', () => {
    const items = [ask('q'), reply('a')];
    expect(withoutEmptyRuns(items, true)).toBe(items);
  });

  it('leaves out a resumed run of only its band, the resume notice and the agent\'s reply', () => {
    const items = [ask('q'), reply('a'), ...emptyRun(), ...emptyRun(), band(), notice(), ask('next'), reply('b')];
    expect(texts(withoutEmptyRuns(items, true))).toEqual([
      'user:q', 'assistant:a', 'resumed', 'activity:System: you have been resumed', 'user:next', 'assistant:b',
    ]);
  });

  it('counts reasoning and a closed row as nothing happening', () => {
    const items: ChatItem[] = [ask('q'), band(), notice(), { kind: 'thinking', text: 'Ready.', seq: ++seq }, reply('Ready.'), { kind: 'closed', reason: 'ended', seq: ++seq }, band()];
    expect(texts(withoutEmptyRuns(items, true))).toEqual(['user:q', 'resumed']);
  });

  it('keeps a resumed run where something happened', () => {
    const happenings: ChatItem[] = [
      ask('q'),
      { kind: 'tool', name: 'query', input: {}, seq: 0, status: 'done' },
      { kind: 'error', text: 'API Error', seq: 0 },
      { kind: 'switched', seq: 0, control: 'speed', option: 'fast' },
      { kind: 'compacting', seq: 0 },
      { kind: 'activity', text: 'System: the instructions were updated', seq: 0, system: true },
      { kind: 'activity', text: 'Uploaded a.csv', seq: 0 },
      { kind: 'permission', requestId: 'r', toolName: 'Bash', input: {}, answered: null, seq: 0 },
    ];
    for (const happening of happenings) {
      const items = [ask('first'), band(), notice(), { ...happening, seq: ++seq } as ChatItem, reply('done'), ...emptyRun()];
      expect(withoutEmptyRuns(items, true)).toHaveLength(items.length);
    }
  });

  it('keeps the last run while it is the current one, and leaves it out otherwise', () => {
    const items = [ask('q'), reply('a'), ...emptyRun(), ...emptyRun()];
    expect(texts(withoutEmptyRuns(items, true))).toEqual([
      'user:q', 'assistant:a', 'resumed', 'activity:System: you have been resumed', "assistant:OK, I'm ready.",
    ]);
    expect(texts(withoutEmptyRuns(items, false))).toEqual(['user:q', 'assistant:a']);
  });

  it('always keeps the first run, even one where only the agent spoke', () => {
    const items = [reply('Hello.'), ...emptyRun()];
    expect(texts(withoutEmptyRuns(items, false))).toEqual(['assistant:Hello.']);
  });

  it('keeps the items pinned below the conversation, and they do not count', () => {
    const queued: ChatItem = { kind: 'user', text: 'later', seq: ++seq, queued: true, queueId: 'q1' };
    const items = [ask('q'), ...emptyRun(), queued];
    expect(texts(withoutEmptyRuns(items, false))).toEqual(['user:q', 'user:later']);
  });
});
