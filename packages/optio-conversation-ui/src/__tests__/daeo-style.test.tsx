import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { DaeoStyleProvider, spinnerRegistry } from '@deai-network/daeo-style-for-vultus';
import { SpinnerScope } from 'vultus-antd';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import type { ChatItem, ChatState } from '../chat.js';

// The conversation view inside a host app that uses daeo-style through its
// vultus bridge (@deai-network/daeo-style-for-vultus), as excavator renders it:
// DaeoStyleProvider puts Daeo's antd theme on the page and lends DaeoSpinner
// through vultus's spinner slot. The Ant Design X widgets must take both: a
// running ThoughtChain tool node shows the lent DaeoSpinner (keyed by the
// view's scope and the call id) instead of X's own loading icon.

function inDaeo(node: ReactNode) {
  return (
    <DaeoStyleProvider theme="light">
      <SpinnerScope name="optio:process:p1">{node}</SpinnerScope>
    </DaeoStyleProvider>
  );
}

function makeState(items: ChatItem[], over: Partial<ChatState> = {}): ChatState {
  return { items, busy: false, closed: false, controls: [], ...over };
}

function makeProps(over: Partial<ConversationViewProps> = {}): ConversationViewProps {
  return {
    state: makeState([]),
    closed: false,
    busy: false,
    toolVerbosity: 'verbose',
    thinkingVerbosity: 'visible',
    showFileUpload: false,
    maxUploadBytes: 10_000_000,
    fileDownload: false,
    onSend: vi.fn(async () => true),
    onInterrupt: vi.fn(),
    onPermission: vi.fn(),
    onFileDownload: vi.fn(),
    ...over,
  };
}

/** The spinner keys DaeoSpinner has mounted on the page. */
const mountedKeys = () => spinnerRegistry.snapshot().entries.filter((e) => e.mounts > 0).map((e) => e.key);

beforeEach(() => {
  spinnerRegistry.reset();
});

describe('the conversation view under daeo-style (the vultus bridge)', () => {
  it('puts the Daeo theme on the page', () => {
    render(inDaeo(<ConversationView {...makeProps()} />));
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('a running tool node shows a DaeoSpinner keyed by the scope and its call id, not X\'s loading icon', async () => {
    const row: ChatItem = { kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 4, status: 'running', callId: 'toolu_9' };
    render(inDaeo(<ConversationView {...makeProps({ state: makeState([row]) })} />));
    await waitFor(() => expect(mountedKeys()).toContain('optio:process:p1/optio:tool:toolu_9'));
    const chain = screen.getByTestId('tool-chain');
    expect(chain.querySelector('[class*="status-loading"]')).toBeNull();
    expect(screen.getByTestId('tool-call').closest('[data-testid="tool-chain"]')).toBe(chain);
  });

  it('a finished tool node keeps X\'s success icon and mounts no tool spinner', async () => {
    const row: ChatItem = {
      kind: 'tool', name: 'Bash', input: { command: 'ls' }, seq: 4, status: 'done', callId: 'toolu_9',
      startedAt: 1, endedAt: 2, result: 'a\nb',
    } as ChatItem;
    render(inDaeo(<ConversationView {...makeProps({ state: makeState([row]) })} />));
    expect(screen.getByTestId('tool-chain').querySelector('[class*="status-success"]')).not.toBeNull();
    expect(mountedKeys().filter((k) => k.includes('optio:tool:'))).toEqual([]);
  });

  it('the working indicator is a DaeoSpinner under the view\'s scope', async () => {
    const items: ChatItem[] = [{ kind: 'user', text: 'go', seq: 1 } as ChatItem];
    render(inDaeo(<ConversationView {...makeProps({ busy: true, state: makeState(items, { busy: true }) })} />));
    await waitFor(() => expect(mountedKeys()).toContain('optio:process:p1/optio:working'));
  });

  it('the permission card shows the tool input as code blocks under the Daeo theme', async () => {
    const card: ChatItem = {
      kind: 'permission', requestId: 'r1', toolName: 'Bash', seq: 2, answered: null,
      input: { command: 'rm -rf /tmp/x', description: 'Clean the scratch directory' },
    };
    render(inDaeo(<ConversationView {...makeProps({ state: makeState([card]) })} />));
    expect(screen.getByText(/Clean the scratch directory/)).toBeTruthy();
    // The command is a syntax-highlighted code block (X CodeHighlighter, its
    // highlighting loaded lazily), not a key-value table.
    await waitFor(() => {
      const code = Array.from(document.querySelectorAll('.ant-codeHighlighter-code')).map((c) => c.textContent).join('\n');
      expect(code).toContain('rm -rf /tmp/x');
    });
    expect(document.querySelector('table')).toBeNull();
  });
});
