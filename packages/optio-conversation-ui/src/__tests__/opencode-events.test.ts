import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatState } from '../chat.js';
import { historyToChatItems, reduceOpencodeEvent } from '../opencode/events.js';
import fixtureEvents from './fixtures/opencode-events.json';
import fixtureHistory from './fixtures/opencode-history.json';

// Recorded /global/event frames wrap the event as {directory?, project?, payload};
// unwrap to find the session id (the reducer itself unwraps the same way).
const SID = (fixtureEvents as any[])
  .map((e) => e.payload ?? e)
  .find((e: any) => e.properties?.sessionID)?.properties.sessionID;

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceOpencodeEvent(s, ev, i, SID), from);
}

describe('opencode event reducer (recorded fixtures)', () => {
  it('full recorded session produces a coherent chat', () => {
    const s = play(fixtureEvents as any[]);
    expect(s.items.some((i) => i.kind === 'user')).toBe(true);
    expect(s.items.some((i) => i.kind === 'assistant' && !i.pending)).toBe(true);
    expect(s.items.some((i) => i.kind === 'permission')).toBe(true);
    expect(s.busy).toBe(false);          // ends on session.status idle
    expect(s.closed).toBe(false);
  });

  it('deltas stream into a pending bubble; part.updated is authoritative', () => {
    const s = play([
      { type: 'session.status', properties: { sessionID: SID, status: { type: 'busy' } } },
      { type: 'message.part.delta', properties: { sessionID: SID, messageID: 'm1', partID: 'p1', delta: 'He' } },
      { type: 'message.part.delta', properties: { sessionID: SID, messageID: 'm1', partID: 'p1', delta: 'llo' } },
    ]);
    const bubble = s.items.find((i) => i.kind === 'assistant');
    expect(bubble && bubble.kind === 'assistant' && bubble.text).toBe('Hello');
    expect(bubble && bubble.kind === 'assistant' && bubble.pending).toBe(true);
    const s2 = play(
      [{ type: 'message.part.updated', properties: { part: { id: 'p1', messageID: 'm1', sessionID: SID, type: 'text', text: 'Hello world' } } }],
      s,
    );
    const b2 = s2.items.find((i) => i.kind === 'assistant');
    expect(b2 && b2.kind === 'assistant' && b2.text).toBe('Hello world');
  });

  it('assistant message completion finalizes the bubble', () => {
    const s = play([
      { type: 'message.part.updated', properties: { part: { id: 'p1', messageID: 'm1', sessionID: SID, type: 'text', text: 'done' } } },
      { type: 'message.updated', properties: { info: { id: 'm1', sessionID: SID, role: 'assistant', time: { created: 1, completed: 2 } } } },
    ]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
  });

  it('tool part renders an ephemeral tool row', () => {
    const s = play([
      { type: 'message.part.updated', properties: { part: { id: 't1', messageID: 'm1', sessionID: SID, type: 'tool', callID: 'c1', tool: 'bash', state: { status: 'running', input: { command: 'echo hi' } } } } },
    ]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.name).toBe('bash');
  });

  it('permission.asked creates a card; permission.replied answers it', () => {
    const ask = { type: 'permission.asked', properties: { id: 'per_1', sessionID: SID, permission: 'bash', patterns: [], metadata: { command: 'rm -rf' }, always: [] } };
    const s = play([ask]);
    const card = s.items.find((i) => i.kind === 'permission');
    expect(card && card.kind === 'permission' && card.requestId).toBe('per_1');
    expect(card && card.kind === 'permission' && card.answered).toBe(null);
    const s2 = play([{ type: 'permission.replied', properties: { sessionID: SID, requestID: 'per_1', reply: 'reject' } }], s);
    const card2 = s2.items.find((i) => i.kind === 'permission');
    expect(card2 && card2.kind === 'permission' && card2.answered).toBe('deny');
  });

  it('other-session events are ignored', () => {
    const s = play([
      { type: 'message.part.delta', properties: { sessionID: 'ses_other', messageID: 'mx', partID: 'px', delta: 'noise' } },
    ]);
    expect(s.items).toHaveLength(0);
  });

  it('history bootstrap maps user/assistant/tool parts', () => {
    const items = historyToChatItems(fixtureHistory as any[], SID);
    expect(items.some((i) => i.kind === 'user')).toBe(true);
    expect(items.some((i) => i.kind === 'assistant')).toBe(true);
  });
});

describe('opencode harness-message mute', () => {
  it('renders a System:-prefixed user message as an activity row, not a user bubble (live path)', () => {
    const s = play([
      { type: 'message.updated', properties: { info: { id: 'mu1', sessionID: SID, role: 'user' } } },
      { type: 'message.part.updated', properties: { part: { id: 'up1', messageID: 'mu1', sessionID: SID, type: 'text', text: 'System: you have been resumed' } } },
    ]);
    expect(s.items.some((i) => i.kind === 'user')).toBe(false);
    const act = s.items.find((i) => i.kind === 'activity');
    expect(act && act.kind === 'activity' && act.text).toBe('System: you have been resumed');
  });

  it('still renders a normal user message as a user bubble (live path)', () => {
    const s = play([
      { type: 'message.updated', properties: { info: { id: 'mu9', sessionID: SID, role: 'user' } } },
      { type: 'message.part.updated', properties: { part: { id: 'up9', messageID: 'mu9', sessionID: SID, type: 'text', text: 'what is 2+2?' } } },
    ]);
    expect(s.items.some((i) => i.kind === 'activity')).toBe(false);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('what is 2+2?');
  });

  it('maps a System:-prefixed user message to an activity row (history path)', () => {
    const items = historyToChatItems([
      { info: { id: 'mu1', sessionID: SID, role: 'user' }, parts: [{ type: 'text', text: 'System: you have been resumed' }] },
    ], SID);
    expect(items.some((i) => i.kind === 'user')).toBe(false);
    expect(items.some((i) => i.kind === 'activity' && i.text === 'System: you have been resumed')).toBe(true);
  });

  it('splits a System: upload notice into a clean bubble + persistent attachment row (live path)', () => {
    const s = play([
      { type: 'message.updated', properties: { info: { id: 'mu2', sessionID: SID, role: 'user' } } },
      { type: 'message.part.updated', properties: { part: { id: 'up2', messageID: 'mu2', sessionID: SID, type: 'text', text: 'System: upload received, stored in uploads/doc.md\n\nplease review' } } },
    ]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('please review');
    const attach = s.items.find((i) => i.kind === 'activity');
    expect(attach && attach.kind === 'activity' && attach.text).toBe('📎 Attached: doc.md');
    expect(s.items.findIndex((i) => i.kind === 'activity')).toBeLessThan(
      s.items.findIndex((i) => i.kind === 'user'),
    );
  });

  it('splits a System: upload notice into a clean bubble + persistent attachment row (history path — the resume guarantee)', () => {
    const items = historyToChatItems([
      { info: { id: 'mu3', sessionID: SID, role: 'user' }, parts: [{ type: 'text', text: 'System: upload received, stored in uploads/doc.md\n\nplease review' }] },
    ], SID);
    const u = items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('please review');
    const attach = items.find((i) => i.kind === 'activity');
    expect(attach && attach.kind === 'activity' && attach.text).toBe('📎 Attached: doc.md');
    expect(items.findIndex((i) => i.kind === 'activity')).toBeLessThan(
      items.findIndex((i) => i.kind === 'user'),
    );
  });
});
