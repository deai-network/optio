import { describe, expect, it } from 'vitest';
import { initialChatState, type ChatItem, type ChatState } from '../chat.js';
import { reduceAntigravityEvent } from '../antigravity/events.js';

// The antigravity reducer consumes the RAW transcript.jsonl lines the listener
// fans out over SSE — Antigravity has NO live transport, so a "turn" is
// synthesised from one `agy -p` invocation + the structured transcript file
// (optio-antigravity conversation.py). The line shapes below are the REAL agy
// schema (captured from the real binary; see antigravity-real-wire.test.ts and
// fixtures/antigravity-real-transcript.jsonl):
//   * USER_INPUT (source USER_EXPLICIT): `content` wrapped in <USER_REQUEST>…
//   * PLANNER_RESPONSE (source MODEL): `content` (answer, may be absent),
//     `thinking` (reasoning), `tool_calls` [{name, args}].
//   * tool-result lines (source MODEL, e.g. LIST_DIRECTORY): `content` = result.
//   * CHECKPOINT / CONVERSATION_HISTORY / GENERIC / SYSTEM_MESSAGE: bookkeeping.
// There is no token streaming (the coalesced PLANNER_RESPONSE content is the
// whole answer for a turn) and no separate turn-end frame (the answer landing
// IS the turn end). The synthetic x-optio-* events (local echo / control update
// / permission answered / closed) are shared with every other engine.

function play(events: any[], from: ChatState = initialChatState): ChatState {
  return events.reduce((s, ev, i) => reduceAntigravityEvent(s, ev, i), from);
}

const userInput = (text: string) => ({
  step_index: 0,
  source: 'USER_EXPLICIT',
  type: 'USER_INPUT',
  status: 'DONE',
  content: `<USER_REQUEST>\n${text}\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nlocal time…\n</ADDITIONAL_METADATA>`,
});
const planner = (fields: { content?: string; thinking?: string; tool_calls?: any[] }) => ({
  source: 'MODEL',
  type: 'PLANNER_RESPONSE',
  status: 'DONE',
  ...fields,
});
const toolResult = (type: string, content: string) => ({ source: 'MODEL', type, status: 'DONE', content });

describe('antigravity transcript event reducer (real agy schema)', () => {
  it('a PLANNER_RESPONSE content line renders a finalized answer bubble and clears busy', () => {
    // One `agy -p` turn delivers its answer at once (no streaming, no turn-end
    // frame) — the content line is both the answer and the turn end.
    const s = play([{ type: 'x-optio-local-user', text: 'say PONG' }, planner({ content: 'PONG' })]);
    const b = s.items.find((i) => i.kind === 'assistant');
    expect(b && b.kind === 'assistant' && b.text).toBe('PONG');
    expect(b && b.kind === 'assistant' && b.pending).toBe(false);
    expect(s.busy).toBe(false);
  });

  it('USER_INPUT renders ONLY the text between the USER_REQUEST tags (metadata dropped)', () => {
    const s = play([userInput('do the thing please')]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('do the thing please');
    expect(s.busy).toBe(true);
  });

  it('USER_INPUT with no USER_REQUEST tags falls back to the raw content', () => {
    const s = play([{ source: 'USER_EXPLICIT', type: 'USER_INPUT', content: '  bare prompt  ' }]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('bare prompt');
  });

  it('a PLANNER_RESPONSE thinking string renders a distinct reasoning row', () => {
    const s = play([planner({ thinking: 'let me consider the request', content: 'ok' })]);
    const t = s.items.find((i) => i.kind === 'thinking');
    expect(t && t.kind === 'thinking' && t.text).toBe('let me consider the request');
  });

  it('a PLANNER_RESPONSE tool_call renders a persisted tool row carrying its args', () => {
    // Unlike a live progress indicator, a transcript tool call is a durable part
    // of the record: the answer bubble must NOT drop it.
    const s = play([
      planner({ tool_calls: [{ name: 'list_dir', args: { DirectoryPath: '/w', toolAction: 'Listing' } }] }),
      planner({ content: 'done' }),
    ]);
    const t = s.items.find((i) => i.kind === 'tool');
    expect(t && t.kind === 'tool' && t.name).toBe('list_dir');
    expect(t && t.kind === 'tool' && t.input).toEqual({ description: 'Listing', DirectoryPath: '/w', toolAction: 'Listing' });
    // still present after the answer arrived
    expect(s.items.some((i) => i.kind === 'tool')).toBe(true);
  });

  it('a tool-result line (source MODEL, e.g. LIST_DIRECTORY) folds into the preceding tool call', () => {
    const s = play([
      planner({ tool_calls: [{ name: 'list_dir', args: { DirectoryPath: '/w' } }] }),
      toolResult('LIST_DIRECTORY', 'Empty directory'),
    ]);
    const tools = s.items.filter((i) => i.kind === 'tool');
    expect(tools).toHaveLength(1); // folded, not a second row
    expect((tools[0] as any).input).toEqual({ DirectoryPath: '/w', result: 'Empty directory' });
  });

  it('the answer coalesces into ONE per-turn bubble across several PLANNER_RESPONSE lines (last wins)', () => {
    // The real "PONG" turn emits content before AND after its tool calls; the
    // rendered answer is one bubble, not one-per-line.
    const s = play([
      userInput('reply PONG then list files'),
      planner({ content: 'PONG', tool_calls: [{ name: 'list_dir', args: {} }] }),
      planner({ tool_calls: [{ name: 'list_permissions', args: {} }] }),
      planner({ content: 'PONG' }),
    ]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1);
    expect((bubbles[0] as any).text).toBe('PONG');
    expect(s.items.filter((i) => i.kind === 'tool').map((t) => (t as any).name)).toEqual([
      'list_dir',
      'list_permissions',
    ]);
    expect(s.busy).toBe(false);
  });

  it('CHECKPOINT / CONVERSATION_HISTORY / GENERIC / SYSTEM_MESSAGE lines are ignored and do not break coalescing', () => {
    const s = play([
      userInput('go'),
      { source: 'SYSTEM', type: 'CONVERSATION_HISTORY' },
      planner({ content: 'first' }),
      { source: 'SYSTEM', type: 'CHECKPOINT', content: '{{ CHECKPOINT 0 }}' },
      { source: 'MODEL', type: 'GENERIC', content: 'some listing output' },
      { source: 'SYSTEM', type: 'SYSTEM_MESSAGE', content: 'System: server restarted' },
      planner({ content: 'final' }),
    ]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1); // still one coalesced bubble
    expect((bubbles[0] as any).text).toBe('final');
  });

  it('the wire USER_INPUT echo confirms the optimistic local bubble in place (no duplicate)', () => {
    // The view optimistically renders x-optio-local-user on send; the transcript
    // then replays a USER_INPUT line for the same turn. Rendering both would
    // double the operator's message — dedupe by (unwrapped) text.
    const s = play([{ type: 'x-optio-local-user', text: 'hello' }, userInput('hello')]);
    const users = s.items.filter((i) => i.kind === 'user');
    expect(users).toHaveLength(1);
    expect((users[0] as any).local).toBeUndefined();
  });

  it('x-optio-local-user renders an optimistic user bubble and sets busy', () => {
    const s = play([{ type: 'x-optio-local-user', text: 'hello' }]);
    const u = s.items.find((i) => i.kind === 'user');
    expect(u && u.kind === 'user' && u.text).toBe('hello');
    expect(u && u.kind === 'user' && u.local).toBe(true);
    expect(s.busy).toBe(true);
  });

  it('a second turn opens a fresh answer bubble instead of appending to the first', () => {
    const s = play([
      userInput('first'),
      planner({ content: 'one' }),
      userInput('second'),
      planner({ content: 'two' }),
    ]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles.map((b) => (b as any).text)).toEqual(['one', 'two']);
    expect(s.busy).toBe(false);
  });

  it('x-optio-control-update folds a model pick into state.controls', () => {
    const from: ChatState = {
      ...initialChatState,
      controls: [{ id: 'model', kind: 'select', label: 'Model', value: 'gemini-a', options: [] }],
    };
    const s = play([{ type: 'x-optio-control-update', id: 'model', value: 'gemini-b' }], from);
    expect(s.controls.find((c) => c.id === 'model')?.value).toBe('gemini-b');
  });

  it('x-optio-closed appends a closed divider and ends the session', () => {
    const s = play([
      userInput('hi'),
      planner({ content: 'bye' }),
      { type: 'x-optio-closed', reason: 'process ended' },
    ]);
    expect(s.closed).toBe(true);
    expect(s.busy).toBe(false);
    expect(s.items.some((i) => i.kind === 'closed')).toBe(true);
  });

  it('an unparseable transcript line is ignored (forward-compat no-op)', () => {
    const s = play([{ type: 'x-optio-unparseable', line: '{bad' }, planner({ content: 'still fine' })]);
    const bubbles = s.items.filter((i) => i.kind === 'assistant');
    expect(bubbles).toHaveLength(1);
  });
});

describe('antigravity answer polish (agy quirks)', () => {
  it('rewrites a file:// deliverable link into an optio-file: download link', () => {
    let s = initialChatState;
    s = reduceAntigravityEvent(s, planner({
      content: 'Done. [numbers.txt](file:///w/home/.gemini/scratch/numbers.txt)',
    }), 1);
    const ans = s.items.find((i) => i.kind === 'assistant') as any;
    expect(ans.text).toContain('](optio-file:/w/home/.gemini/scratch/numbers.txt)');
    expect(ans.text).not.toContain('file://');
  });

  it('surfaces toolAction/toolSummary as a description (dequoted)', () => {
    let s = initialChatState;
    s = reduceAntigravityEvent(s, planner({
      tool_calls: [{ name: 'run_command', args: { Command: 'ls -la', toolAction: '"Running ls"' } }],
    }), 1);
    const t = s.items.find((i) => i.kind === 'tool') as any;
    expect(t.input.description).toBe('Running ls');   // dequoted
    expect(t.input.Command).toBe('ls -la');           // raw args preserved
  });
});
