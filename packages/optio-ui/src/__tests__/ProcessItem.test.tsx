import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

import { ProcessItem } from '../components/ProcessItem.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

const MESSAGE = 'Resurrecting: saving the unsaved work…';

function renderItem(process: any) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ProcessItem process={{ _id: '1', name: 'P', ...process }} />
    </I18nextProvider>,
  );
}

describe('ProcessItem progress message', () => {
  it('is shown for a failed process while it is resurrecting', () => {
    renderItem({
      status: { state: 'failed' }, resurrecting: true,
      progress: { percent: null, message: MESSAGE },
    });
    expect(screen.getByText(`— ${MESSAGE}`)).toBeTruthy();
  });

  it('is not shown for a failed process that is not resurrecting', () => {
    renderItem({ status: { state: 'failed' }, progress: { percent: null, message: MESSAGE } });
    expect(screen.queryByText(`— ${MESSAGE}`)).toBeNull();
  });
});
