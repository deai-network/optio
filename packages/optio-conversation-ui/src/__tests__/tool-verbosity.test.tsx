import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { initialChatState, type ChatItem, type ChatState } from '../chat.js';

// The shared render applies the tool-verbosity rules for ALL 7 agents (each
// reducer only supplies the ChatState). These pin the four levels × lifecycle:
//   silent                    → never a tool row
//   description-while-active   → row WHILE running, gone once finished
//   description-only           → persistent one-line row (⟳/✓/✗)
//   verbose                    → line + detail; finished collapses (click to open)

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
const detail = () => document.querySelector('[data-testid="tool-call"] table');

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
  it('hides the row once finished', () => {
    view([toolItem({ status: 'done' })], 'description-while-active');
    expect(toolRow()).toBeNull();
  });
});

describe('tool verbosity — description-only', () => {
  it('shows a persistent row for running / done / failed with the right status', () => {
    for (const [status, expected] of [['running', 'running'], ['done', 'done'], ['failed', 'failed']] as const) {
      view([toolItem({ status })], 'description-only');
      expect(toolRow()).not.toBeNull();
      expect(toolRow()!.getAttribute('data-tool-status')).toBe(expected);
      expect(detail()).toBeNull(); // no args table at this level
      cleanup();
    }
  });
});

describe('tool verbosity — verbose', () => {
  it('shows the detail while running', () => {
    view([toolItem({ status: 'running' })], 'verbose');
    expect(detail()).not.toBeNull();
  });
  it('collapses the detail once finished, click re-expands', () => {
    view([toolItem({ status: 'done' })], 'verbose');
    // Finished → collapsed: the row is present but the args table is hidden.
    expect(toolRow()).not.toBeNull();
    expect(detail()).toBeNull();
    // Click the line → expands.
    fireEvent.click(toolRow()!.querySelector('div')!);
    expect(detail()).not.toBeNull();
  });
});

describe('tool verbosity — cross-reducer finished detection (no ACP status)', () => {
  it('treats codex-style input.status=completed as finished', () => {
    view([toolItem({ input: { command: 'x', status: 'completed' } })], 'description-while-active');
    expect(toolRow()).toBeNull(); // hidden = detected finished via input.status
  });
  it('treats antigravity-style input.result as finished', () => {
    view([toolItem({ input: { result: 'ok' } })], 'description-while-active');
    expect(toolRow()).toBeNull();
  });
  it('treats a tool with no signal as still running', () => {
    view([toolItem({ input: {} })], 'description-while-active');
    expect(toolRow()).not.toBeNull();
  });
});
