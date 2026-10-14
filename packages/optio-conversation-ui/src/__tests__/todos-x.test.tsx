import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';

// Ant Design X experiment (owner-approved 2026-10-12): the task list is one X
// ThoughtChain, like the tool calls: a node per task, linked by X's line, with
// X's status icons (completed: success, failed: error, cancelled: abort,
// in progress: X's loading icon, or the app's spinner when it lends one;
// pending: X's plain node icon). The node's title carries the row (label,
// data-status, and for a described task the info icon and the click that
// opens the detail panel).

function view(todos: ConversationViewProps['todos'], closed = false) {
  const props: ConversationViewProps = {
    state: { items: [{ kind: 'user', text: 'hi', seq: 1 }], busy: false, closed, controls: [] },
    closed, busy: false, toolVerbosity: 'verbose', thinkingVerbosity: 'visible',
    showFileUpload: false, maxUploadBytes: 1, fileDownload: false,
    onSend: vi.fn(async () => true), onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload: vi.fn(),
    todos,
  };
  render(<ConversationView {...props} />);
}

const panel = () => screen.getByTestId('conversation-todos-panel');
const nodes = () => [...panel().querySelectorAll('.ant-thought-chain-node')] as HTMLElement[];
const statusOf = (node: HTMLElement) =>
  /ant-thought-chain-status-(\w+)/.exec(node.querySelector('.ant-thought-chain-status')?.className ?? '')?.[1] ?? null;

describe('the task list as an X ThoughtChain', () => {
  it('is one chain, a node per task in order, titled with its label', () => {
    view([
      { id: '1', text: 'Write', status: 'in_progress' },
      { id: '2', text: 'Ship', status: 'pending' },
      { id: '3', text: 'Done', status: 'completed' },
    ]);
    expect(panel().querySelectorAll('.ant-thought-chain')).toHaveLength(1);
    expect(nodes().map((n) => n.querySelector('[data-testid="conversation-todo"]')?.textContent)).toEqual(['Write', 'Ship', 'Done']);
  });

  it("shows X's status icons: success, error for (failed), abort for cancelled, none while pending", () => {
    view([
      { id: '1', text: 'Done', status: 'completed' },
      { id: '2', text: 'Deploy (failed)', status: 'completed' },
      { id: '3', text: 'Skip', status: 'cancelled' },
      { id: '4', text: 'Later', status: 'pending' },
    ]);
    expect(nodes().map(statusOf)).toEqual(['success', 'error', 'abort', null]);
    // The (failed) suffix is carried by the icon, not shown in the label.
    expect(nodes()[1].querySelector('[data-testid="conversation-todo"]')?.textContent).toBe('Deploy');
  });

  it("a task in progress shows X's loading icon and blinks", () => {
    view([{ id: '1', text: 'Write', status: 'in_progress' }]);
    expect(statusOf(nodes()[0])).toBe('loading');
    expect(nodes()[0].querySelector('[class*="motion-blink"]')).not.toBeNull();
  });

  it('in a closed conversation (a suspended session) a task in progress neither spins nor blinks', () => {
    view([{ id: '1', text: 'Write', status: 'in_progress' }], true);
    expect(statusOf(nodes()[0])).not.toBe('loading');
    expect(nodes()[0].querySelector('[class*="motion-blink"]')).toBeNull();
    expect(screen.queryByTestId('conversation-todo-spinner')).toBeNull();
  });

  // X draws its connecting line beside a node's body, below the title row; a
  // task node is title-only, so each gets room below its title (X's item
  // style) and the chain no gap, so the line runs into the next task (owner,
  // 2026-10-12: the tool calls had it, the task list did not).
  it("gives each task room below its title for X's connecting line", () => {
    view([
      { id: '1', text: 'Write', status: 'completed' },
      { id: '2', text: 'Ship', status: 'pending' },
    ]);
    for (const node of nodes()) expect(node.style.paddingBottom).toBe('10px');
    expect((panel().querySelector('.ant-thought-chain') as HTMLElement).style.gap).toBe('0px');
  });

  it('a described task keeps its info icon, and a click on it opens the detail panel', () => {
    view([{ id: '1', text: 'Get the source', status: 'pending', description: 'Ask for the **source**.' }]);
    const row = nodes()[0].querySelector('[data-testid="conversation-todo"]') as HTMLElement;
    expect(row.tagName).toBe('BUTTON');
    expect(row.querySelector('[data-testid="conversation-todo-info"]')).not.toBeNull();
    fireEvent.click(row);
    expect(screen.getByTestId('conversation-todo-detail').style.width).toBe('360px');
  });
});
