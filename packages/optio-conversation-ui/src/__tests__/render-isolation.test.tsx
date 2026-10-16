import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import type { ReactElement } from 'react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import type { ChatItem, ChatState } from '../chat.js';

// Typing, the elapsed-time ticker, a streaming delta and a busy/idle toggle
// must not re-render the whole transcript (a long chat took seconds per
// keystroke when they did). This counts the renders of the expensive leaves
// (AnswerBlock: the markdown; CodeBlock: a highlighted block) and of the X
// components every transcript row is drawn with (Bubble, its System and
// Divider variants, ThoughtChain), around one such event each.

const counts = vi.hoisted(() => ({ answer: 0, code: 0, bubble: 0, chain: 0 }));
type Counter = keyof typeof counts;

// The real component, counted. A memo'd export keeps its memo (and compare),
// so only the renders that get past it are counted.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function counted(React: any, Comp: any, name: Counter): any {
  const isMemo = Comp.$$typeof === Symbol.for('react.memo');
  const inner = isMemo ? Comp.type : Comp;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Counted = (props: any) => {
    counts[name]++;
    return inner(props);
  };
  return isMemo ? React.memo(Counted, Comp.compare) : Counted;
}

// An X component (a forwardRef) counted, with its statics (Bubble.System, ...).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function countedX(React: any, Comp: any, name: Counter): any {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Counted = React.forwardRef((props: any, ref: any) => {
    counts[name]++;
    return React.createElement(Comp, { ...props, ref });
  });
  for (const key of Object.keys(Comp)) {
    if (key !== '$$typeof' && key !== 'render' && key !== 'displayName') Counted[key] = Comp[key];
  }
  return Counted;
}

vi.mock('../AnswerBlock.js', async (importOriginal) => {
  const React = await import('react');
  const mod = await importOriginal<typeof import('../AnswerBlock.js')>();
  return { ...mod, AnswerBlock: counted(React, mod.AnswerBlock, 'answer') };
});
vi.mock('../CodeBlock.js', async (importOriginal) => {
  const React = await import('react');
  const mod = await importOriginal<typeof import('../CodeBlock.js')>();
  return { ...mod, CodeBlock: counted(React, mod.CodeBlock, 'code') };
});
vi.mock('@ant-design/x', async (importOriginal) => {
  const React = await import('react');
  const mod = await importOriginal<typeof import('@ant-design/x')>();
  const Bubble = countedX(React, mod.Bubble, 'bubble');
  Bubble.System = countedX(React, mod.Bubble.System, 'bubble');
  Bubble.Divider = countedX(React, mod.Bubble.Divider, 'bubble');
  return { ...mod, Bubble, ThoughtChain: countedX(React, mod.ThoughtChain, 'chain') };
});

function reset() {
  for (const k of Object.keys(counts) as Counter[]) counts[k] = 0;
}

// ---------------------------------------------------------------------------
// A 40-row conversation: questions, markdown answers with code blocks, tool
// calls (verbose: their input and output are code blocks too) and System rows.

const T0 = Date.UTC(2026, 9, 15, 8, 0, 0);

const ANSWERS = [
  'The **loader** reads each file in turn:\n\n- keyed by `path`;\n- a miss parses again.\n\nNothing else touches the cache.',
  'The fix:\n\n```ts\nexport function load(paths: string[]): Row[] {\n  let attempts = 0;\n  return paths.flatMap((p) => read(p, attempts++));\n}\n```\n\nThen rerun the tests.',
  'And the check:\n\n```python\ndef summarize(df):\n    return df.groupby("region").sum()\n```\n\nBoth agree.',
];

const TOOLS: Array<{ name: string; input: unknown; result: string }> = [
  { name: 'Bash', input: { command: 'pnpm vitest run src/loader.test.ts', description: 'Run the loader tests' }, result: 'Tests  12 passed (12)' },
  { name: 'Read', input: { file_path: 'src/loader.ts' }, result: '1\texport function load() {}\n2\t' },
  { name: 'Edit', input: { file_path: 'src/loader.ts', old_string: 'a', new_string: 'b' }, result: 'The file src/loader.ts has been updated.' },
];

const PATTERN = ['user', 'assistant', 'tool', 'tool', 'assistant', 'user', 'assistant', 'tool', 'assistant', 'activity'] as const;

function makeItems(n: number): ChatItem[] {
  const items: ChatItem[] = [];
  let a = 0;
  let t = 0;
  for (let i = 0; i < n; i++) {
    const seq = i + 1;
    const ts = T0 + i * 20_000;
    const kind = PATTERN[i % PATTERN.length];
    if (kind === 'user') items.push({ kind: 'user', seq, timestamp: ts, text: `Question ${seq}: why are rows dropped?` });
    else if (kind === 'assistant') {
      items.push({ kind: 'assistant', seq, msgId: `m${seq}`, pending: false, timestamp: ts, endTimestamp: ts + 15_000, text: ANSWERS[a++ % ANSWERS.length] });
    } else if (kind === 'tool') {
      const tool = TOOLS[t++ % TOOLS.length];
      items.push({ kind: 'tool', seq, name: tool.name, input: tool.input, result: tool.result, status: 'done', callId: `c${seq}`, startedAt: ts, endedAt: ts + 2_300 });
    } else items.push({ kind: 'activity', seq, system: true, timestamp: ts, text: 'System: a background task finished (exit 0)' });
  }
  return items;
}

const N = 40;

function makeState(items: ChatItem[], over: Partial<ChatState> = {}): ChatState {
  return { items, busy: false, closed: false, controls: [], ...over };
}

// The callbacks are new on every render, as an engine view passes them.
function makeProps(state: ChatState, over: Partial<ConversationViewProps> = {}): ConversationViewProps {
  return {
    state,
    closed: false,
    busy: false,
    toolVerbosity: 'verbose',
    thinkingVerbosity: 'visible',
    showFileUpload: false,
    maxUploadBytes: 10_000_000,
    fileDownload: true,
    onSend: async () => true,
    onSteer: async () => true,
    onInterrupt: () => {},
    onPermission: () => {},
    onFileDownload: () => {},
    ...over,
  };
}

function view(props: ConversationViewProps): ReactElement {
  return <ConfigProvider><ConversationView {...props} /></ConfigProvider>;
}

function counted0() {
  return { answer: counts.answer, code: counts.code, bubble: counts.bubble, chain: counts.chain };
}

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe('ConversationView render isolation (40 rows)', () => {
  it('a keystroke in the composer re-renders no transcript row', () => {
    const items = makeItems(N);
    render(view(makeProps(makeState(items))));
    expect(counts.answer).toBeGreaterThan(0);
    expect(counts.code).toBeGreaterThan(0);

    const box = screen.getByTestId('conversation-input-box') as HTMLTextAreaElement;
    reset();
    fireEvent.change(box, { target: { value: 'h' } });
    expect(box.value).toBe('h');
    expect(counted0()).toEqual({ answer: 0, code: 0, bubble: 0, chain: 0 });
  });

  it('a tick of a running tool row re-renders only that row', () => {
    const items = makeItems(N);
    const r = render(view(makeProps(makeState(items))));
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] });
    vi.setSystemTime(T0 + N * 20_000 + 3_000);
    const running: ChatItem = {
      kind: 'tool', seq: N + 1, name: 'Bash', callId: 'running',
      input: { command: 'sleep 600', description: 'Wait for the import' },
      status: 'running', startedAt: T0 + N * 20_000,
    };
    act(() => { r.rerender(view(makeProps(makeState([...items, running])))); });
    const elapsed = () => screen.getAllByTestId('tool-elapsed').pop()?.textContent;
    expect(elapsed()).toBe(' · 3s');

    reset();
    act(() => { vi.advanceTimersByTime(1000); });
    expect(elapsed()).toBe(' · 4s');
    const after = counted0();
    expect({ answer: after.answer, code: after.code, bubble: after.bubble }).toEqual({ answer: 0, code: 0, bubble: 0 });
    expect(after.chain).toBeLessThanOrEqual(1);
  });
});
