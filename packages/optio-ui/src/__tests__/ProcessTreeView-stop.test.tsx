import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

import { ProcessTreeView } from '../components/ProcessTreeView.js';

// The tree's stop button and status badge: a process whose task can resume
// is suspended (its state is saved), others are cancelled.
const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

function node(extra: Record<string, unknown>) {
  return {
    _id: 'n1', name: 'Analyze', parentId: null, depth: 0, order: 0, children: [],
    status: { state: 'running' }, progress: { percent: null }, cancellable: true, ...extra,
  } as any;
}
function renderTree(tree: any) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ProcessTreeView treeData={tree} sseState={{ connected: true }} onCancel={() => {}} />
    </I18nextProvider>,
  );
}

describe('ProcessTreeView: the stop button', () => {
  it('a resumable process: "Suspend", a pause icon, not red', () => {
    renderTree(node({ supportsResume: true }));
    const button = screen.getByRole('button', { name: 'Suspend' });
    expect(button.className).not.toContain('ant-btn-dangerous');
    expect(button.querySelector('.anticon-pause-circle')).not.toBeNull();
  });

  it('other processes: "Cancel", red', () => {
    renderTree(node({}));
    const button = screen.getByRole('button', { name: 'processes.cancel' });
    expect(button.className).toContain('ant-btn-dangerous');
  });

  it('the badge of a suspended process says so', () => {
    renderTree(node({ status: { state: 'cancelled' }, supportsResume: true, hasSavedState: true, cancellable: false }));
    expect(screen.getByText('Suspended')).toBeTruthy();
  });
});

describe('ProcessTreeView: onProcessClick', () => {
  const tree = node({ children: [node({ _id: 'n2', name: 'Step two', parentId: 'n1', depth: 1 })] });

  it("a node's name is a link button calling it with the node's _id", () => {
    const onProcessClick = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <ProcessTreeView treeData={tree} sseState={{ connected: true }} onProcessClick={onProcessClick} />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Step two' }));
    expect(onProcessClick).toHaveBeenCalledWith('n2');
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    expect(onProcessClick).toHaveBeenLastCalledWith('n1');
  });

  it('without the prop, the names are plain text: no link', () => {
    renderTree(tree);
    expect(screen.getByText('Step two')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Step two' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Analyze' })).toBeNull();
  });
});
