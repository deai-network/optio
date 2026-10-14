import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
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
