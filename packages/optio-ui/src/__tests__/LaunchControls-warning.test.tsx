import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

vi.mock('../hooks/useProcessActions.js', () => ({
  useProcessActions: () => ({ launchAsync: vi.fn(), dismissAsync: vi.fn() }),
}));

import { LaunchControls } from '../components/LaunchControls.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

const WARNING = 'This wipes the data and loads it again from scratch.';

function renderWith(process: any) {
  const onLaunch = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <LaunchControls process={process} onLaunch={onLaunch} size="small" />
    </I18nextProvider>,
  );
  return onLaunch;
}

// A task's warning asks before its launch: the play button's click only opens
// the confirmation, its OK is the one launch.
describe('LaunchControls: a task with a warning', () => {
  const process = { _id: '1', status: { state: 'idle' }, warning: WARNING };

  function clickPlay() {
    fireEvent.click(screen.getByRole('button'));
  }

  it('the click asks and launches nothing', async () => {
    const onLaunch = renderWith(process);
    clickPlay();
    expect(await screen.findByText(WARNING)).toBeTruthy();
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('OK launches once', async () => {
    const onLaunch = renderWith(process);
    clickPlay();
    await screen.findByText(WARNING);
    fireEvent.click(screen.getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(onLaunch).toHaveBeenCalled());
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('1', undefined);
  });

  it('Cancel launches nothing', async () => {
    const onLaunch = renderWith(process);
    clickPlay();
    await screen.findByText(WARNING);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('without a warning the click launches at once', () => {
    const onLaunch = renderWith({ _id: '2', status: { state: 'idle' } });
    clickPlay();
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('2', undefined);
    expect(screen.queryByRole('button', { name: 'OK' })).toBeNull();
  });
});
