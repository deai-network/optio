import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatState } from '../chat.js';
import { reduceGrokEvent } from '../grok/events.js';

// The grok reducer consumes the RAW ACP JSON-RPC objects the listener fans out
// over SSE: session/update notifications, the session/request_permission
// request, the session/prompt response (turn-end), plus the synthetic
// x-optio-* events. Shapes mirror the wire pinned in conversation.py.

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceGrokEvent(s, ev, i), from);
}

const chunk = (text: string) => ({
  jsonrpc: '2.0',
  method: 'session/update',
  params: { sessionId: 's1', update: { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text } } },
});
const thought = (text: string) => ({
  jsonrpc: '2.0',
  method: 'session/update',
  params: { sessionId: 's1', update: { sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text } } },
});
const turnEnd = (id: number, stopReason = 'end_turn') => ({
  jsonrpc: '2.0', id, result: { stopReason },
});

describe('grok ACP event reducer', () => {
  it('agent_message_chunk deltas accumulate into one pending bubble', () => {
    const s = play([chunk('PO'), chunk('NG')]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(true);
    expect(s.busy).toBe(true);
  });

  it('turn-end (session/prompt response) finalizes the bubble and clears busy', () => {
    const s = play([chunk('done'), turnEnd(1)]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });

  it('a second turn opens a fresh bubble instead of appending to the first', () => {
    const s = play([chunk('first'), turnEnd(1), chunk('second'), turnEnd(2)]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['first', 'second']);
  });

  it('agent_thought_chunk renders as a muted activity row, not in the answer', () => {
    const s = play([thought('reasoning...'), chunk('ANSWER'), turnEnd(1)]);
    const activity = s.items.find((i) => i.kind === 'activity');
    expect(activity && activity.kind === 'activity' && activity.text).toContain('reasoning');
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('ANSWER');
  });

  it('tool_call renders a tool row named by its title with its rawInput', () => {
    const s = play([{
      jsonrpc: '2.0', method: 'session/update',
      params: { sessionId: 's1', update: {
        sessionUpdate: 'tool_call', toolCallId: 'tc1', title: 'Shell',
        rawInput: { command: 'echo hi' } } },
    }]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.name).toBe('Shell');
    expect(t && t.kind === 'tool' && (t.input as any).command).toBe('echo hi');
  });

  it('tool_call_update updates the same tool row by toolCallId', () => {
    const s = play([
      { jsonrpc: '2.0', method: 'session/update', params: { sessionId: 's1', update: {
        sessionUpdate: 'tool_call', toolCallId: 'tc1', title: 'Shell', rawInput: { command: 'echo hi' } } } },
      { jsonrpc: '2.0', method: 'session/update', params: { sessionId: 's1', update: {
        sessionUpdate: 'tool_call_update', toolCallId: 'tc1', kind: 'execute',
        title: 'Shell (done)', status: 'completed' } } },
    ]);
    const tools = s.items.filter((i) => i.kind === 'tool');
    expect(tools).toHaveLength(1);
    expect(tools[0].kind === 'tool' && tools[0].name).toBe('Shell (done)');
    // rawInput not resent → prior input preserved.
    expect(tools[0].kind === 'tool' && (tools[0].input as any).command).toBe('echo hi');
  });

  it('tool items carry the full rawInput dict for verbose KV rendering (Stage 7)', () => {
    // The shared ConversationView renders every key of `item.input` as a
    // key→value table at verbose verbosity, so the reducer must preserve the
    // WHOLE rawInput object (not a summary), and tool_call_update must merge a
    // resent rawInput while keeping the prior one when it is omitted.
    const s = play([
      { jsonrpc: '2.0', method: 'session/update', params: { sessionId: 's1', update: {
        sessionUpdate: 'tool_call', toolCallId: 'tc1', title: 'Shell',
        rawInput: { command: 'grep -r x .', cwd: '/w', timeout: 30 } } } },
    ]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.input).toEqual({ command: 'grep -r x .', cwd: '/w', timeout: 30 });

    // update WITHOUT rawInput → prior full input preserved.
    const s2 = play([
      { jsonrpc: '2.0', method: 'session/update', params: { sessionId: 's1', update: {
        sessionUpdate: 'tool_call_update', toolCallId: 'tc1', status: 'completed' } } },
    ], s);
    const t2 = s2.items.find((i) => i.kind === 'tool');
    expect(t2 && t2.kind === 'tool' && t2.input).toEqual({ command: 'grep -r x .', cwd: '/w', timeout: 30 });

    // update WITH a resent rawInput → merged (replaced).
    const s3 = play([
      { jsonrpc: '2.0', method: 'session/update', params: { sessionId: 's1', update: {
        sessionUpdate: 'tool_call_update', toolCallId: 'tc1', rawInput: { command: 'grep -r y .' } } } },
    ], s);
    const t3 = s3.items.find((i) => i.kind === 'tool');
    expect(t3 && t3.kind === 'tool' && (t3.input as any).command).toBe('grep -r y .');
  });

  it('session/request_permission creates a card; x-optio-permission-answered flips it', () => {
    const ask = {
      jsonrpc: '2.0', id: 99, method: 'session/request_permission',
      params: { sessionId: 's1', toolCall: {
        toolCallId: 'tc1', kind: 'execute', title: 'Execute `echo hi`',
        rawInput: { command: 'echo hi' } },
        options: [
          { optionId: 'allow-once', name: 'Yes', kind: 'allow_once' },
          { optionId: 'reject-once', name: 'No', kind: 'reject_once' }] },
    };
    const s = play([ask]);
    const card = s.items.find((i) => i.kind === 'permission');
    expect(card && card.kind === 'permission' && card.requestId).toBe('99');
    expect(card && card.kind === 'permission' && card.toolName).toBe('Execute `echo hi`');
    expect(card && card.kind === 'permission' && card.answered).toBe(null);
    expect(s.busy).toBe(true); // parked on the gate

    const s2 = play([{ type: 'x-optio-permission-answered', request_id: '99', behavior: 'deny' }], s);
    const card2 = s2.items.find((i) => i.kind === 'permission');
    expect(card2 && card2.kind === 'permission' && card2.answered).toBe('deny');
  });

  it('x-optio-local-user renders an optimistic user bubble and sets busy', () => {
    const s = play([{ type: 'x-optio-local-user', text: 'hello' }]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('hello');
    expect(u && u.kind === 'user' && u.local).toBe(true);
    expect(s.busy).toBe(true);
  });

  it('x-optio-closed appends a closed divider and ends the session', () => {
    const s = play([chunk('bye'), turnEnd(1), { type: 'x-optio-closed', reason: 'process ended' }]);
    expect(s.closed).toBe(true);
    expect(s.busy).toBe(false);
    expect(s.items.some((i) => i.kind === 'closed')).toBe(true);
  });

  it('a JSON-RPC error response surfaces an error item', () => {
    const s = play([{ jsonrpc: '2.0', id: 3, error: { code: -32000, message: 'boom' } }]);
    const e = s.items.find((i) => i.kind === 'error');
    expect(e && e.kind === 'error' && e.text).toContain('boom');
    expect(s.busy).toBe(false);
  });

  it('a full turn: local echo → thought → answer → turn-end', () => {
    const s = play([
      { type: 'x-optio-local-user', text: 'say PONG' },
      thought('let me think'),
      chunk('PO'), chunk('NG'),
      turnEnd(1),
    ]);
    const kinds = s.items.map((i) => i.kind);
    expect(kinds).toContain('user');
    expect(kinds).toContain('activity');
    expect(kinds).toContain('assistant');
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });
});
