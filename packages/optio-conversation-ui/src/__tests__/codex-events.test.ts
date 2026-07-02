import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatState } from '../chat.js';
import { reduceCodexEvent } from '../codex/events.js';

// The codex reducer consumes the RAW app-server JSON-RPC objects the listener
// fans out over SSE: item/turn notifications, the item/*/requestApproval
// server requests, JSON-RPC error responses, plus the synthetic x-optio-*
// events. Shapes mirror the wire pinned in optio-codex's conversation.py
// (codex-cli 0.142.5 probe + schemas). The "jsonrpc" field is omitted on the
// wire — the fixtures omit it too.

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceCodexEvent(s, ev, i), from);
}

const delta = (text: string, itemId = 'i-msg', turnId = 'turn-1') => ({
  method: 'item/agentMessage/delta',
  params: { threadId: 't1', turnId, itemId, delta: text },
});
const reasoning = (text: string) => ({
  method: 'item/reasoning/summaryTextDelta',
  params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-r', delta: text, summaryIndex: 0 },
});
const itemStarted = (item: any, turnId = 'turn-1') => ({
  method: 'item/started',
  params: { threadId: 't1', turnId, item, startedAtMs: 0 },
});
const itemCompleted = (item: any, turnId = 'turn-1') => ({
  method: 'item/completed',
  params: { threadId: 't1', turnId, item, completedAtMs: 0 },
});
const turnCompleted = (status = 'completed', error?: any) => ({
  method: 'turn/completed',
  params: { threadId: 't1', turn: { id: 'turn-1', status, items: [], error: error ?? null } },
});
const cmdItem = { type: 'commandExecution', id: 'i-cmd', command: 'echo hi', cwd: '/w', status: 'inProgress' };

describe('codex app-server event reducer', () => {
  it('agentMessage deltas accumulate into one pending bubble', () => {
    const s = play([delta('PO'), delta('NG')]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(true);
    expect(s.busy).toBe(true);
  });

  it('turn/completed finalizes the bubble and clears busy', () => {
    const s = play([delta('done'), turnCompleted()]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });

  it('a second turn opens a fresh bubble instead of appending to the first', () => {
    const s = play([delta('first'), turnCompleted(), delta('second', 'i-2', 'turn-2'), turnCompleted()]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['first', 'second']);
  });

  it('item/completed agentMessage text is authoritative (heals delta gaps)', () => {
    const s = play([
      delta('PO'), // "NG" delta lost
      itemCompleted({ type: 'agentMessage', id: 'i-msg', text: 'PONG' }),
      turnCompleted(),
    ]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
  });

  it('reasoning deltas render as one coalesced activity row, not in the answer', () => {
    const s = play([reasoning('thinking'), reasoning(' more'), delta('ANSWER'), turnCompleted()]);
    const acts = s.items.filter((i) => i.kind === 'activity');
    expect(acts).toHaveLength(1);
    expect(acts[0].kind === 'activity' && acts[0].text).toBe('thinking more');
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('ANSWER');
  });

  it('item/started commandExecution renders a tool row named by the command', () => {
    const s = play([itemStarted(cmdItem)]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.name).toBe('echo hi');
    expect(t && t.kind === 'tool' && (t.input as any).command).toBe('echo hi');
    expect(t && t.kind === 'tool' && (t.input as any).cwd).toBe('/w');
    expect(s.busy).toBe(true);
  });

  it('item/completed updates the same tool row by item id (status merged)', () => {
    const s = play([
      itemStarted(cmdItem),
      itemCompleted({ ...cmdItem, status: 'completed', exitCode: 0 }),
    ]);
    const tools = s.items.filter((i) => i.kind === 'tool');
    expect(tools).toHaveLength(1);
    expect(tools[0].kind === 'tool' && (tools[0].input as any).status).toBe('completed');
    expect(tools[0].kind === 'tool' && (tools[0].input as any).exitCode).toBe(0);
    // prior fields preserved for verbose KV rendering
    expect(tools[0].kind === 'tool' && (tools[0].input as any).command).toBe('echo hi');
  });

  it('fileChange / mcpToolCall / webSearch items render tool rows', () => {
    const s = play([
      itemStarted({ type: 'fileChange', id: 'i-fc', status: 'inProgress',
        changes: [{ path: 'a.txt', kind: 'edit', diff: '' }] }),
      itemStarted({ type: 'mcpToolCall', id: 'i-mcp', server: 'srv', tool: 'fetch',
        status: 'inProgress', arguments: { url: 'https://x' } }),
      itemStarted({ type: 'webSearch', id: 'i-ws', query: 'codex docs' }),
    ]);
    const names = s.items.filter((i) => i.kind === 'tool').map((t) => (t as any).name);
    expect(names).toEqual(['file change', 'srv.fetch', 'web search']);
  });

  it('tool rows are ephemeral: new assistant text drops them', () => {
    const s = play([itemStarted(cmdItem), delta('done running')]);
    expect(s.items.some((i) => i.kind === 'tool')).toBe(false);
    expect(s.items.some((i) => i.kind === 'assistant')).toBe(true);
  });

  it('requestApproval creates a card; x-optio-permission-answered flips it; busy stays true', () => {
    const ask = {
      id: 99, method: 'item/commandExecution/requestApproval',
      params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-cmd',
        command: 'echo hi', cwd: '/w', reason: null, startedAtMs: 0 },
    };
    const s = play([itemStarted(cmdItem), ask]);
    const card = s.items.find((i) => i.kind === 'permission');
    expect(card && card.kind === 'permission' && card.requestId).toBe('99');
    expect(card && card.kind === 'permission' && card.toolName).toBe('echo hi');
    expect(card && card.kind === 'permission' && card.answered).toBe(null);
    expect(s.busy).toBe(true); // parked on the gate
    expect(s.items.some((i) => i.kind === 'tool')).toBe(false); // superseded

    const s2 = play([{ type: 'x-optio-permission-answered', request_id: '99', behavior: 'deny' }], s);
    const card2 = s2.items.find((i) => i.kind === 'permission');
    expect(card2 && card2.kind === 'permission' && card2.answered).toBe('deny');
  });

  it('fileChange requestApproval names the card "file change"', () => {
    const s = play([{
      id: 41, method: 'item/fileChange/requestApproval',
      params: { threadId: 't1', turnId: 'turn-1', itemId: 'i-fc', reason: null, startedAtMs: 0 },
    }]);
    const card = s.items.find((i) => i.kind === 'permission');
    expect(card && card.kind === 'permission' && card.toolName).toBe('file change');
  });

  it('x-optio-local-user renders an optimistic user bubble and sets busy', () => {
    const s = play([{ type: 'x-optio-local-user', text: 'hello' }]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('hello');
    expect(u && u.kind === 'user' && u.local).toBe(true);
    expect(s.busy).toBe(true);
  });

  it('x-optio-closed appends a closed divider and ends the session', () => {
    const s = play([delta('bye'), turnCompleted(), { type: 'x-optio-closed', reason: 'process ended' }]);
    expect(s.closed).toBe(true);
    expect(s.busy).toBe(false);
    expect(s.items.some((i) => i.kind === 'closed')).toBe(true);
  });

  it('a JSON-RPC error response surfaces an error item and clears busy', () => {
    const s = play([{ type: 'x-optio-local-user', text: 'go' },
      { id: 3, error: { code: -32001, message: 'Server overloaded; retry later.' } }]);
    const e = s.items.find((i) => i.kind === 'error');
    expect(e && e.kind === 'error' && e.text).toContain('overloaded');
    expect(s.busy).toBe(false);
  });

  it('an error notification surfaces an error item; turn/completed failed carries the message too', () => {
    const s = play([
      { method: 'error', params: { threadId: 't1', turnId: 'turn-1',
        error: { message: 'quota exceeded', codexErrorInfo: 'UsageLimitExceeded' }, willRetry: false } },
      turnCompleted('failed', { message: 'quota exceeded' }),
    ]);
    expect(s.items.filter((i) => i.kind === 'error').length).toBeGreaterThanOrEqual(1);
    expect(s.busy).toBe(false);
  });

  it('handshake responses and unrendered notifications are no-ops', () => {
    const s = play([
      { id: 1, result: { userAgent: 'codex/0.142.5' } },
      { method: 'thread/started', params: { thread: { id: 't1' } } },
      { method: 'turn/started', params: { threadId: 't1', turn: { id: 'turn-1', status: 'inProgress', items: [] } } },
      { method: 'thread/tokenUsage/updated', params: { threadId: 't1' } },
    ]);
    expect(s.items).toHaveLength(0);
    expect(s.busy).toBe(false);
  });

  it('a full turn: local echo → reasoning → tool → answer → turn end', () => {
    const s = play([
      { type: 'x-optio-local-user', text: 'say PONG' },
      reasoning('let me think'),
      itemStarted(cmdItem),
      itemCompleted({ ...cmdItem, status: 'completed', exitCode: 0 }),
      delta('PO'), delta('NG'),
      turnCompleted(),
    ]);
    const kinds = s.items.map((i) => i.kind);
    expect(kinds).toContain('user');
    expect(kinds).toContain('activity');
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });
});
