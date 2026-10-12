import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { initialChatState, type ChatItem, type ChatState } from '../chat.js';

// The shared render applies the tool-verbosity rules for ALL 7 agents (each
// reducer only supplies the ChatState). These pin the four levels × lifecycle:
//   silent                    → never a tool row
//   description-while-active   → a collapsed row while the tool runs; none once
//                                it finished (a finished background job keeps
//                                its one muted outcome line, as at silent)
//   description-only           → same collapsible row, starts collapsed
//   verbose                    → same collapsible row, starts expanded

afterEach(cleanup);

const toolItem = (over: Partial<Extract<ChatItem, { kind: 'tool' }>> = {}): ChatItem => ({
  kind: 'tool', name: 'Bash', input: { command: 'echo hi' }, seq: 1, ...over,
});

function view(items: ChatItem[], toolVerbosity: ConversationViewProps['toolVerbosity']) {
  const state: ChatState = { ...initialChatState, items };
  const props: ConversationViewProps = {
    state, closed: false, busy: false,
    toolVerbosity, thinkingVerbosity: 'hidden',
    showFileUpload: false, maxUploadBytes: 0, fileDownload: false,
    onSend: vi.fn().mockResolvedValue(true), onInterrupt: vi.fn(),
    onPermission: vi.fn(), onFileDownload: vi.fn(),
  };
  return render(<ConversationView {...props} />);
}

const toolRow = () => screen.queryByTestId('tool-call');
// A tool row is an X ThoughtChain node: the tool name is its title (a click
// there toggles the row) and the detail is its collapsible content, the input
// as code blocks (here the Bash command as a bash block).
const toolNode = () => toolRow()?.closest('.ant-thought-chain-node') ?? null;
const detail = () => toolNode()?.querySelector('.ant-thought-chain-node-content .ant-codeHighlighter') ?? null;
const toggle = () => fireEvent.click(toolNode()!.querySelector('.ant-thought-chain-node-title')!);
// A collapse runs X's leave motion before the content is removed.
const collapsed = () => waitFor(() => expect(detail()).toBeNull());

describe('tool verbosity — silent', () => {
  it('renders no tool row, running or finished', () => {
    view([toolItem({ status: 'running' })], 'silent');
    expect(toolRow()).toBeNull();
    cleanup();
    view([toolItem({ status: 'done' })], 'silent');
    expect(toolRow()).toBeNull();
  });
});

describe('tool verbosity — description-while-active', () => {
  it('shows the row while running', () => {
    view([toolItem({ status: 'running' })], 'description-while-active');
    expect(toolRow()).not.toBeNull();
    expect(toolRow()!.getAttribute('data-tool-status')).toBe('running');
  });
  it('a running row starts collapsed, and a click opens the detail', () => {
    view([toolItem({ status: 'running' })], 'description-while-active');
    expect(detail()).toBeNull();
    toggle();
    expect(detail()).not.toBeNull();
    expect(detail()!.textContent).toContain('echo hi');
  });
  it('hides the row once the tool finished (done, failed or stopped)', () => {
    for (const status of ['done', 'failed', 'stopped'] as const) {
      view([toolItem({ status })], 'description-while-active');
      expect(toolRow()).toBeNull();
      cleanup();
    }
  });
  it('of several calls, shows only the running ones', () => {
    view([
      toolItem({ seq: 1, status: 'done' }),
      toolItem({ seq: 2, status: 'running', input: { command: 'sleep 9' } }),
      toolItem({ seq: 3, status: 'done' }),
    ], 'description-while-active');
    const rows = screen.queryAllByTestId('tool-call');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-tool-status')).toBe('running');
    expect(rows[0].textContent).toContain('sleep 9');
  });
  it('a finished background job keeps its one muted outcome line', () => {
    view([toolItem({ status: 'done', background: true, result: 'exit 0' })], 'description-while-active');
    expect(toolRow()).toBeNull();
    expect(screen.getByTestId('background-finished').textContent).toContain('exit 0');
  });
});

describe('tool verbosity — description-only', () => {
  it('shows a persistent row for running / done / failed with the right status', () => {
    for (const [status, expected] of [['running', 'running'], ['done', 'done'], ['failed', 'failed']] as const) {
      view([toolItem({ status })], 'description-only');
      expect(toolRow()).not.toBeNull();
      expect(toolRow()!.getAttribute('data-tool-status')).toBe(expected);
      expect(detail()).toBeNull(); // collapsed: no input blocks at this level
      cleanup();
    }
  });
});

describe('tool verbosity — verbose', () => {
  it('shows the detail while running', () => {
    view([toolItem({ status: 'running' })], 'verbose');
    expect(detail()).not.toBeNull();
    expect(detail()!.textContent).toContain('echo hi');
  });
  it('starts expanded once finished, and a click collapses', async () => {
    view([toolItem({ status: 'done' })], 'verbose');
    expect(toolRow()).not.toBeNull();
    expect(detail()).not.toBeNull();
    toggle();
    await collapsed();
  });
  it('a running row starts expanded, and a click collapses it', async () => {
    view([toolItem({ status: 'running' })], 'verbose');
    expect(detail()).not.toBeNull();
    toggle();
    await collapsed();
  });
});

describe('tool verbosity — cross-reducer finished detection (no ACP status)', () => {
  it('treats codex-style input.status=completed as finished', () => {
    view([toolItem({ input: { command: 'x', status: 'completed' } })], 'description-only');
    expect(toolRow()!.getAttribute('data-tool-status')).toBe('done');
    expect(detail()).toBeNull();
  });
  it('treats antigravity-style input.result as finished', () => {
    view([toolItem({ input: { result: 'ok' } })], 'description-only');
    expect(toolRow()!.getAttribute('data-tool-status')).toBe('done');
    expect(detail()).toBeNull();
  });
  it('treats a tool with no signal as still running', () => {
    view([toolItem({ input: {} })], 'description-while-active');
    expect(toolRow()).not.toBeNull();
  });
});
