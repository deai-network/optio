import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

import { ProcessStatusBadge } from '../components/ProcessStatusBadge.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

function renderBadge(props: any) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ProcessStatusBadge {...props} />
    </I18nextProvider>,
  );
}

describe('ProcessStatusBadge auto-resume indicator', () => {
  it('shows the stopwatch indicator when autoResumeScheduled is true', () => {
    renderBadge({ state: 'cancelled', autoResumeScheduled: true });
    expect(screen.getByLabelText('Scheduled for auto-restart')).toBeTruthy();
  });

  it('does not show the indicator when autoResumeScheduled is false/absent', () => {
    renderBadge({ state: 'cancelled' });
    expect(screen.queryByLabelText('Scheduled for auto-restart')).toBeNull();
  });
});

describe('ProcessStatusBadge: a resumable process suspends', () => {
  // Stopping a process whose task can resume saves its state: it suspends.
  const tag = () => document.querySelector('.ant-tag') as HTMLElement;

  it.each(['cancel_requested', 'cancelling'])('%s: "Suspending", in blue', (state) => {
    renderBadge({ state, supportsResume: true, hasSavedState: true });
    expect(screen.getByText('Suspending')).toBeTruthy();
    expect(tag().className).toContain('ant-tag-blue');
  });

  it('stopped with its state saved: "Suspended", in blue', () => {
    renderBadge({ state: 'cancelled', supportsResume: true, hasSavedState: true });
    expect(screen.getByText('Suspended')).toBeTruthy();
    expect(tag().className).toContain('ant-tag-blue');
  });

  it('stopped without a saved state: still cancelled', () => {
    renderBadge({ state: 'cancelled', supportsResume: true, hasSavedState: false });
    expect(screen.queryByText('Suspended')).toBeNull();
    expect(screen.getByText('cancelled')).toBeTruthy();
  });

  it('a process that cannot resume: cancelling, as before', () => {
    renderBadge({ state: 'cancelling' });
    expect(screen.queryByText('Suspending')).toBeNull();
    expect(screen.getByText('cancelling')).toBeTruthy();
    expect(tag().className).toContain('ant-tag-orange');
  });
});

