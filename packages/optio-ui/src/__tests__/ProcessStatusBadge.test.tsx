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
