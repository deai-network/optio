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

  it('reasoning INTERLEAVED between agentMessage items stays ONE answer bubble', () => {
    // GPT-5 splits a turn across several agentMessage items with reasoning
    // summaries in between (preamble → reasoning → final). The answer must
    // coalesce into a single bubble keyed on the turn's msgId; an interleaved
    // reasoning (activity) row must not split it into a second bubble (the
    // reported regression). A second bubble would also strand a permanent
    // pending:true indicator, since turn/completed only finalizes the first.
    const s = play([
      delta('Hi', 'i-1'),
      reasoning('reconsider'),
      delta(' there', 'i-2'),
      turnCompleted(),
    ]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1);
    expect((bubbles[0] as any).text).toBe('Hi there');
    expect((bubbles[0] as any).pending).toBe(false);
    expect(s.busy).toBe(false);
    // reasoning is still surfaced as a muted activity row, not folded in
    expect(s.items.some((i) => i.kind === 'activity')).toBe(true);
  });

  it('item/started commandExecution renders a tool row named by the command', () => {
    const s = play([itemStarted(cmdItem)]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.name).toBe('echo hi');
    expect(t && t.kind === 'tool' && (t.input as any).command).toBe('echo hi');
    expect(t && t.kind === 'tool' && (t.input as any).cwd).toBe('/w');
    expect(s.busy).toBe(true);
  });

  it('a tool between two agentMessage bubbles splits them into SEPARATE bubbles', () => {
    // Real codex turns interleave: assistant text -> function_call (tool) ->
    // agent_message -> assistant text. A hidden/ephemeral tool still separated
    // the two answers, so they must be two bubbles, not one coalesced blob.
    const s = play([delta('before'), itemStarted(cmdItem), delta('after')]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['before', 'after']);
    // the pre-tool bubble is finalized; only the latest stays pending
    expect((bubbles[0] as any).pending).toBe(false);
    expect((bubbles[1] as any).pending).toBe(true);
  });

  it('a permanent System notice between two agentMessage bubbles splits them', () => {
    const sys = itemCompleted({ type: 'userMessage', id: 'i-u', text: 'System: heads up' });
    const s = play([delta('before'), sys, delta('after')]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['before', 'after']);
    expect((bubbles[0] as any).pending).toBe(false);
  });

  it('an error row between two agentMessage bubbles splits them', () => {
    const err = { type: 'x-optio-local-error', text: 'upload failed' };
    const s = play([delta('before'), err, delta('after')]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['before', 'after']);
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

  it('a replayed userMessage item/completed renders a past user bubble (resume history)', () => {
    // On resume the driver re-emits each prior turn's items as item/completed;
    // the operator's own past prompts arrive as userMessage items and MUST
    // render — else a resumed conversation shows only the agent's past replies
    // (the whole prompts-dropped bug).
    const s = play([
      itemCompleted({ type: 'userMessage', id: 'u1',
        content: [{ type: 'text', text: 'prior question' }] }),
      delta('prior answer'), turnCompleted(),
    ]);
    const users = s.items.filter((i) => i.kind === 'user');
    expect(users).toHaveLength(1);
    expect(users[0].kind === 'user' && users[0].text).toBe('prior question');
    // Not an optimistic echo — this is replayed history.
    expect(users[0].kind === 'user' && users[0].local).toBeFalsy();
  });

  it('a replayed userMessage reads item.text too (rollout shape), not only content[]', () => {
    const s = play([
      itemCompleted({ type: 'userMessage', id: 'u1', text: 'plain-text prompt' }),
    ]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('plain-text prompt');
  });

  it('a live userMessage echo confirms the optimistic bubble instead of duplicating it', () => {
    // In live flow the view already showed the prompt optimistically
    // (x-optio-local-user); real codex then echoes it back as a userMessage
    // item. The reducer must CONFIRM the existing local bubble, not append a
    // second one.
    const s = play([
      { type: 'x-optio-local-user', text: 'say PONG' },
      itemCompleted({ type: 'userMessage', id: 'u1',
        content: [{ type: 'text', text: 'say PONG' }] }),
    ]);
    const users = s.items.filter((i) => i.kind === 'user');
    expect(users).toHaveLength(1);
    expect(users[0].kind === 'user' && users[0].local).toBeFalsy();
  });

  it('a harness System: userMessage renders as an activity row, not a user bubble', () => {
    // Resume notices / harness sends go through the same send() path, so codex
    // echoes them back as userMessage items with a "System: " prefix. They must
    // render as muted activity rows, never user bubbles (mirrors claudecode).
    const s = play([
      itemCompleted({ type: 'userMessage', id: 's1',
        content: [{ type: 'text', text: 'System: you have been resumed' }] }),
    ]);
    expect(s.items.some((i) => i.kind === 'user')).toBe(false);
    const a = s.items.find((i) => i.kind === 'activity');
    expect(a && a.kind === 'activity' && a.text).toBe('System: you have been resumed');
  });

  it('an upload-notice userMessage strips the System notice, confirms the optimistic body, and adds a persistent attachment row', () => {
    // On upload the prompt sent to codex is "System: upload received…\n\n<body>",
    // but the optimistic echo is just <body>. The wire echo strips the notice and
    // confirms the optimistic bubble (no duplicate), AND emits a muted "attached
    // files" activity row naming the file, chronologically before the bubble.
    const s = play([
      { type: 'x-optio-local-user', text: 'summarize this' },
      itemCompleted({ type: 'userMessage', id: 'u1', content: [{ type: 'text',
        text: 'System: upload received, stored in uploads/a.txt\n\nsummarize this' }] }),
    ]);
    const users = s.items.filter((i) => i.kind === 'user');
    expect(users).toHaveLength(1);
    expect(users[0].kind === 'user' && users[0].text).toBe('summarize this');
    expect(users[0].kind === 'user' && users[0].local).toBeFalsy();
    const attach = s.items.find((i) => i.kind === 'activity');
    expect(attach && attach.kind === 'activity' && attach.text).toBe('📎 Attached: a.txt');
    expect(s.items.findIndex((i) => i.kind === 'activity')).toBeLessThan(
      s.items.findIndex((i) => i.kind === 'user'),
    );
  });

  it('replays the attachment row from a resumed userMessage (no optimistic echo)', () => {
    // The resume guarantee: on session load the driver replays the userMessage
    // WITHOUT a preceding optimistic echo, so the reducer must reconstruct both
    // the clean bubble and the persistent attachment row from history alone.
    const s = play([
      itemCompleted({ type: 'userMessage', id: 'u9', content: [{ type: 'text',
        text: 'System: upload received, stored in uploads/spec.md\n\nimplement it' }] }),
    ]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('implement it');
    const attach = s.items.find((i) => i.kind === 'activity');
    expect(attach && attach.kind === 'activity' && attach.text).toBe('📎 Attached: spec.md');
    expect(s.items.findIndex((i) => i.kind === 'activity')).toBeLessThan(
      s.items.findIndex((i) => i.kind === 'user'),
    );
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
