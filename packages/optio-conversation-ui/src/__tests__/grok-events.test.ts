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
const userChunk = (text: string) => ({
  jsonrpc: '2.0',
  method: 'session/update',
  params: { sessionId: 's1', update: { sessionUpdate: 'user_message_chunk', content: { type: 'text', text } } },
});

describe('ask_user_question', () => {
  const ask = {
    jsonrpc: '2.0',
    id: 11,
    method: '_x.ai/ask_user_question',
    params: {
      sessionId: 's1',
      toolCallId: 'tc',
      mode: 'default',
      questions: [{
        question: 'Continue?',
        options: [{ label: 'Wait', description: 'stop here' }],
      }],
    },
  };

  it('becomes a question card the operator can answer', () => {
    const s = play([ask]);
    const q = s.items.find((i) => i.kind === 'question');
    expect(q).toMatchObject({
      kind: 'question',
      requestId: '11',
      mode: 'default',
      answered: null,
      questions: [{ question: 'Continue?', options: [{ label: 'Wait', description: 'stop here' }] }],
    });
    expect(s.busy).toBe(true);
  });

  it('marks the card answered when the listener reports the outcome', () => {
    const open = play([ask]);
    const s = reduceGrokEvent(open, {
      type: 'x-optio-question-answered', request_id: '11', outcome: 'accepted',
    }, 1);
    const q = s.items.find((i) => i.kind === 'question');
    expect(q && q.kind === 'question' && q.answered).toBe('accepted');
  });
});

describe('turn end', () => {
  it('turn_completed clears the working flag and finalizes the answer', () => {
    const s = play([
      chunk('The background sleep has finished.'),
      {
        jsonrpc: '2.0', method: '_x.ai/session_notification',
        params: { update: { sessionUpdate: 'turn_completed', stop_reason: 'end_turn' } },
      },
    ]);
    expect(s.busy).toBe(false);
    const answer = s.items.find((i) => i.kind === 'assistant');
    expect(answer && answer.kind === 'assistant' && answer.pending).toBe(false);
  });

  it('a prompt response after turn_completed does not end the turn twice', () => {
    const events = [
      chunk('done'),
      {
        jsonrpc: '2.0', method: '_x.ai/session_notification',
        params: { update: { sessionUpdate: 'turn_completed', stop_reason: 'end_turn' } },
      },
      { jsonrpc: '2.0', id: 1, result: { stopReason: 'end_turn' } },
    ];
    const twice = play(events);
    expect(twice.busy).toBe(false);
    expect(twice.items.filter((i) => i.kind === 'assistant')).toHaveLength(1);
  });
});

describe('grok/cursor shared ACP reducer — resume replay rendering', () => {
  it('a replayed user_message_chunk renders a user bubble (was dropped)', () => {
    const s = play([userChunk('my prior question')]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('my prior question');
    expect(u && u.kind === 'user' && u.local).toBeFalsy();
  });

  it('replayed turns (session/load has NO turn-end) stay SEPARATE, each with its prompt', () => {
    // Live-confirmed on cursor: session/load replays user_message_chunk +
    // agent_message_chunk per turn but no session/prompt turn-end, so the user
    // prompt must delimit turns — else every answer coalesces into one bubble
    // and the prompts vanish (the reported resume bug).
    const s = play([
      userChunk('q1'), chunk('answer one'),
      userChunk('q2'), chunk('answer two'),
    ]);
    expect(s.items.filter((i) => i.kind === 'user').map((u) => (u as any).text)).toEqual(['q1', 'q2']);
    expect(s.items.filter((i) => i.kind === 'assistant').map((a) => (a as any).text))
      .toEqual(['answer one', 'answer two']);
  });

  it('a harness System: user_message_chunk renders as an activity row, not a user bubble', () => {
    const s = play([userChunk('System: you have been resumed')]);
    expect(s.items.some((i) => i.kind === 'user')).toBe(false);
    const a = s.items.find((i) => i.kind === 'activity');
    expect(a && a.kind === 'activity' && a.text).toBe('System: you have been resumed');
  });

  it('a live user_message_chunk echo confirms the optimistic bubble, no duplicate', () => {
    const s = play([{ type: 'x-optio-local-user', text: 'say PONG' }, userChunk('say PONG')]);
    const users = s.items.filter((i) => i.kind === 'user');
    expect(users).toHaveLength(1);
    expect(users[0].kind === 'user' && users[0].local).toBeFalsy();
  });

  it('an injected resume-notice user_message_chunk un-merges the boundary + shows the notice', () => {
    const s = play([
      userChunk('prior question'), chunk('prior answer'),  // replayed turn (pending)
      userChunk('System: you have been resumed'),          // injected boundary
      chunk('sure, I remember'),                            // live resume answer
    ]);
    expect(s.items.filter((i) => i.kind === 'assistant').map((a) => (a as any).text))
      .toEqual(['prior answer', 'sure, I remember']);       // NOT merged
    expect(s.items.some((i) => i.kind === 'activity'
      && (i as any).text === 'System: you have been resumed')).toBe(true);
  });

  it('a duplicate System: user_message_chunk does not double-render the activity row', () => {
    const s = play([
      userChunk('System: you have been resumed'),
      userChunk('System: you have been resumed'),
    ]);
    expect(s.items.filter((i) => i.kind === 'activity').length).toBe(1);
  });
});

describe('grok/cursor shared ACP reducer — upload notice → attachment row', () => {
  it('splits an upload notice into a clean bubble + attachment row, deduping the optimistic echo (live path)', () => {
    const s = play([
      { type: 'x-optio-local-user', text: 'review the screenshot' },
      userChunk('System: upload received, stored in uploads/pic.png\n\nreview the screenshot'),
    ]);
    const users = s.items.filter((i) => i.kind === 'user');
    expect(users).toHaveLength(1);
    expect(users[0].kind === 'user' && users[0].text).toBe('review the screenshot');
    expect(users[0].kind === 'user' && users[0].local).toBeFalsy();
    const attach = s.items.find((i) => i.kind === 'activity');
    expect(attach && attach.kind === 'activity' && attach.text).toBe('📎 Attached: pic.png');
    expect(s.items.findIndex((i) => i.kind === 'activity')).toBeLessThan(
      s.items.findIndex((i) => i.kind === 'user'));
  });

  it('replays the attachment row from a resumed multi-file upload chunk (no optimistic echo — the resume guarantee)', () => {
    const s = play([
      userChunk('System: upload received, stored in uploads/a.md\nSystem: upload received, stored in uploads/b.png\n\nlook at both'),
    ]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('look at both');
    const attach = s.items.find((i) => i.kind === 'activity');
    expect(attach && attach.kind === 'activity' && attach.text).toBe('📎 Attached: a.md, b.png');
    expect(s.items.findIndex((i) => i.kind === 'activity')).toBeLessThan(
      s.items.findIndex((i) => i.kind === 'user'));
  });

  it('surfaces an x-optio-local-error as an error row', () => {
    const s = play([{ type: 'x-optio-local-error', text: 'Upload failed: big.png — exceeds the size limit' }]);
    const e = s.items.find((i) => i.kind === 'error');
    expect(e && e.kind === 'error' && e.text).toBe('Upload failed: big.png — exceeds the size limit');
  });
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
    // grok reasoning models alternate thinking and answering within a turn. The
    // answer must coalesce into a single bubble; interleaved thought (activity)
    // rows must not split it into a bubble-per-token (the reported bug).
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
    expect(kinds).toContain('thinking');
    expect(kinds).toContain('assistant');
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });
});


describe('grok background tasks', () => {
  const id = 'call-1';
  const toolCall = {
    jsonrpc: '2.0', method: 'session/update',
    params: { update: {
      sessionUpdate: 'tool_call', toolCallId: id, title: 'run_terminal_command',
      rawInput: { command: 'sleep 60', description: 'Sleep 60 seconds in the background', background: true },
    } },
  };
  const launched = {
    jsonrpc: '2.0', method: 'session/update',
    params: { update: {
      sessionUpdate: 'tool_call_update', toolCallId: id, status: 'completed',
      title: '[bg] sleep 60',
      content: [{ type: 'content', content: { type: 'text', text: 'Background task started' } }],
    } },
  };
  const backgrounded = {
    jsonrpc: '2.0', method: 'session/update',
    params: { update: {
      sessionUpdate: 'task_backgrounded', tool_call_id: id, task_id: 'task-1',
      description: 'Sleep 60 seconds in the background', command: 'sleep 60',
    } },
  };
  const completed = {
    jsonrpc: '2.0', method: 'session/update',
    params: { update: {
      sessionUpdate: 'task_completed',
      task_snapshot: { task_id: 'task-1', output: 'sleep-60 finished\n' },
    } },
  };

  it('a backgrounded shell stays running until task_completed', () => {
    const mid = play([toolCall, launched, backgrounded]);
    const row = mid.items.find((i) => i.kind === 'tool');
    expect(row).toMatchObject({
      kind: 'tool', background: true, status: 'running', taskId: 'task-1',
    });
    const end = play([toolCall, launched, backgrounded, completed]);
    const done = end.items.find((i) => i.kind === 'tool');
    expect(done).toMatchObject({ kind: 'tool', background: true, status: 'done', result: 'sleep-60 finished' });
  });
});


  it('the real grok wire uses _x.ai/session/update for background lifecycle', () => {
    const id = 'call-real';
    const mid = play([
      {
        jsonrpc: '2.0', method: 'session/update',
        params: { update: {
          sessionUpdate: 'tool_call', toolCallId: id, title: 'run_terminal_command',
          rawInput: { command: 'sleep 60', description: 'Sleep 60 seconds in the background', background: true },
        } },
      },
      {
        jsonrpc: '2.0', method: 'session/update',
        params: { update: { sessionUpdate: 'tool_call_update', toolCallId: id, status: 'completed', title: '[bg] sleep 60' } },
      },
      {
        jsonrpc: '2.0', method: '_x.ai/session/update',
        params: { update: { sessionUpdate: 'task_backgrounded', tool_call_id: id, task_id: 'task-real', description: 'Sleep 60 seconds in the background' } },
      },
    ]);
    expect(mid.items.find((i) => i.kind === 'tool')).toMatchObject({
      background: true, status: 'running', taskId: 'task-real',
    });
    const end = play([
      ...([
        {
          jsonrpc: '2.0', method: 'session/update',
          params: { update: {
            sessionUpdate: 'tool_call', toolCallId: id, title: 'run_terminal_command',
            rawInput: { command: 'sleep 60', description: 'Sleep 60 seconds in the background', background: true },
          } },
        },
        {
          jsonrpc: '2.0', method: 'session/update',
          params: { update: { sessionUpdate: 'tool_call_update', toolCallId: id, status: 'completed', title: '[bg] sleep 60' } },
        },
        {
          jsonrpc: '2.0', method: '_x.ai/session/update',
          params: { update: { sessionUpdate: 'task_backgrounded', tool_call_id: id, task_id: 'task-real' } },
        },
      ]),
      {
        jsonrpc: '2.0', method: '_x.ai/session/update',
        params: { update: { sessionUpdate: 'task_completed', task_snapshot: { task_id: 'task-real', output: 'sleep-60 finished\n' } } },
      },
    ]);
    expect(end.items.find((i) => i.kind === 'tool')).toMatchObject({
      background: true, status: 'done', result: 'sleep-60 finished',
    });
  });


describe('grok tool lines', () => {
  it('names a shell Bash, keeps the short description, and records start and end', () => {
    const s = play([
      {
        jsonrpc: '2.0', method: 'session/update',
        params: {
          _meta: { agentTimestampMs: 1_000 },
          update: {
            sessionUpdate: 'tool_call', toolCallId: 't1', title: 'run_terminal_command',
            rawInput: { command: 'echo hi && sleep 1', description: 'Say hi' },
          },
        },
      },
      {
        jsonrpc: '2.0', method: 'session/update',
        params: {
          _meta: { agentTimestampMs: 2_500 },
          update: {
            sessionUpdate: 'tool_call_update', toolCallId: 't1', kind: 'execute', status: 'completed',
            title: 'Execute `echo hi && sleep 1`',
            rawInput: { command: 'echo hi && sleep 1', description: 'Say hi' },
          },
        },
      },
    ]);
    const row = s.items.find((i) => i.kind === 'tool');
    expect(row).toMatchObject({
      name: 'Bash', status: 'done', startedAt: 1_000, endedAt: 2_500,
    });
    expect(row && row.kind === 'tool' && row.name.includes('echo hi')).toBe(false);
    expect(row && row.kind === 'tool' && (row.input as { description?: string }).description).toBe('Say hi');
  });
});


describe('failed grok tool calls', () => {
  it('keeps the failure text when the same event also carries rawInput', () => {
    const s = play([{
      jsonrpc: '2.0', method: 'session/update',
      params: { update: {
        sessionUpdate: 'tool_call',
        toolCallId: 'ask-1',
        status: 'failed',
        title: 'Ask: How would you like to continue this UI test?',
        rawInput: { variant: 'AskUserQuestion', questions: [{ question: 'How?' }] },
        content: [{ type: 'content', content: { type: 'text', text: 'Tool `ask_user_question` failed: client does not implement it' } }],
      } },
    }]);
    const row = s.items.find((i) => i.kind === 'tool');
    expect(row).toMatchObject({
      status: 'failed',
      result: 'Tool `ask_user_question` failed: client does not implement it',
    });
  });
});


describe('shell output that failed inside a completed tool', () => {
  it('marks a completed /etc/hosts Permission denied as failed and keeps the text', () => {
    const s = play([
      {
        jsonrpc: '2.0', method: 'session/update',
        params: { update: {
          sessionUpdate: 'tool_call', toolCallId: 'h1', title: 'run_terminal_command',
          rawInput: { command: "echo x >> /etc/hosts", description: "Attempt to append a line to /etc/hosts" },
        } },
      },
      {
        jsonrpc: '2.0', method: 'session/update',
        params: { update: {
          sessionUpdate: 'tool_call_update', toolCallId: 'h1', status: 'completed',
          content: [{ type: 'content', content: { type: 'text', text: "--: line 1: /etc/hosts: Permission denied\nexit:1\n" } }],
          rawOutput: { exit_code: 0, output_for_prompt: "exit: 0\n--: line 1: /etc/hosts: Permission denied\nexit:1\n" },
        } },
      },
    ]);
    const row = s.items.find((i) => i.kind === 'tool');
    expect(row).toMatchObject({
      status: 'failed',
      result: "--: line 1: /etc/hosts: Permission denied\nexit:1",
    });
  });
});
