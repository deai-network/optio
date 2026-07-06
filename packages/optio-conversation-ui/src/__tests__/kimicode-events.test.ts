import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatState } from '../chat.js';
import { reduceKimiCodeEvent } from '../kimicode/events.js';

// The kimicode reducer consumes the RAW ACP (Agent Client Protocol, JSON-RPC
// 2.0) objects the listener fans out over SSE — the same wire shapes pinned in
// optio-kimicode's conversation.py: session/update notifications
// (agent_message_chunk, agent_thought_chunk, tool_call, tool_call_update), the
// session/request_permission request, the session/prompt response (turn-end
// carrying stopReason), plus the synthetic x-optio-* events. kimi is a
// reasoning model that interleaves thought and answer deltas within a turn; the
// answer must coalesce into ONE bubble by turn id, not tail position.

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceKimiCodeEvent(s, ev, i), from);
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

describe('kimicode ACP event reducer', () => {
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

  it('a cancelled turn (interrupt) still finalizes the bubble and clears busy', () => {
    // A denied/aborted turn returns stopReason:"cancelled" — still the turn-end.
    const s = play([chunk('partial'), turnEnd(1, 'cancelled')]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });

  it('a second turn opens a fresh bubble instead of appending to the first', () => {
    const s = play([chunk('first'), turnEnd(1), chunk('second'), turnEnd(2)]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['first', 'second']);
  });

  it('agent_thought_chunk renders as a distinct thinking row (not activity/System), not in the answer', () => {
    const s = play([thought('reasoning...'), chunk('ANSWER'), turnEnd(1)]);
    const thinking = s.items.find((i) => i.kind === 'thinking');
    expect(thinking && thinking.kind === 'thinking' && thinking.text).toContain('reasoning');
    // NOT the harness-System 'activity' kind — the view styles/gates them differently.
    expect(s.items.some((i) => i.kind === 'activity')).toBe(false);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('ANSWER');
  });

  it('thought chunks INTERLEAVED with answer chunks stay ONE answer bubble', () => {
    // kimi reasoning models alternate thinking and answering within a turn. The
    // answer must coalesce into a single bubble keyed by turn id; interleaved
    // thought rows must not split it into a bubble-per-token.
    const s = play([
      chunk('Hi'), thought('reconsider'), chunk(' there'), thought('actually'), chunk('!'),
      turnEnd(1),
    ]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1);
    expect((bubbles[0] as any).text).toBe('Hi there!');
    expect((bubbles[0] as any).pending).toBe(false);
    // reasoning is still surfaced (as a distinct 'thinking' row), just not folded into the answer
    expect(s.items.some((i) => i.kind === 'thinking')).toBe(true);
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

  it('tool items carry the full rawInput dict for verbose KV rendering', () => {
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

  it('an empty model list on the session/new response warns that no model is available', () => {
    // session/new returns the unified configOptions. An empty model picker means
    // kimi-code has no LLM configured (not logged in), so every turn fails
    // SILENTLY — it reports model.not_configured as a plain end_turn with no
    // content. Surface it so the operator isn't left staring at silence.
    const s = play([{ jsonrpc: '2.0', id: 2, result: { sessionId: 's1', configOptions: [
      { type: 'select', id: 'model', name: 'Model', category: 'model', currentValue: '', options: [] },
      { type: 'select', id: 'mode', name: 'Mode', category: 'mode', currentValue: 'default',
        options: [{ id: 'default', label: 'Default' }] },
    ] } }]);
    const e = s.items.find((i) => i.kind === 'error');
    expect(e && e.kind === 'error').toBeTruthy();
    expect(e && e.kind === 'error' && e.text.toLowerCase()).toMatch(/model|log/);
  });

  it('a populated model list on session/new renders no warning', () => {
    const s = play([{ jsonrpc: '2.0', id: 2, result: { sessionId: 's1', configOptions: [
      { type: 'select', id: 'model', name: 'Model', category: 'model', currentValue: 'kimi-k2',
        options: [{ id: 'kimi-k2', label: 'Kimi K2' }] },
    ] } }]);
    expect(s.items.some((i) => i.kind === 'error')).toBe(false);
  });

  it('plan / config_option_update notifications are no-ops (no rendered row)', () => {
    // kimi passes plan / available_commands_update / config_option_update /
    // user_message_chunk through untouched — the reducer renders nothing yet.
    const before = play([chunk('hi')]);
    const after = play([
      { jsonrpc: '2.0', method: 'session/update', params: { sessionId: 's1', update: {
        sessionUpdate: 'plan', entries: [{ content: 'step 1', status: 'pending' }] } } },
      { jsonrpc: '2.0', method: 'session/update', params: { sessionId: 's1', update: {
        sessionUpdate: 'config_option_update', id: 'model', currentValue: 'kimi-k2' } } },
    ], before);
    expect(after.items.map((i) => i.kind)).toEqual(before.items.map((i) => i.kind));
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
    expect(kinds).toContain('thinking');
    expect(kinds).toContain('assistant');
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });
});
