import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

// A task factory's form launches through useProcessActions().launchAsync.
const mockLaunchAsync = vi.fn();
vi.mock('../hooks/useProcessActions.js', () => ({
  useProcessActions: () => ({ launchAsync: mockLaunchAsync }),
}));

import { ProcessItem } from '../components/ProcessItem.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

const MESSAGE = 'Resurrecting: saving the unsaved work…';

function renderItem(process: any, onCancel?: (id: string) => void) {
  return render(
    <I18nextProvider i18n={i18n}>
      <ProcessItem process={{ _id: '1', name: 'P', ...process }} onCancel={onCancel} />
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

describe('ProcessItem: the stop button', () => {
  const running = { status: { state: 'running' }, progress: { percent: null }, cancellable: true };

  it('a resumable process is suspended: "Suspend", a pause icon, not red', () => {
    renderItem({ ...running, supportsResume: true }, () => {});
    const button = screen.getByRole('button', { name: 'Suspend' });
    expect(button.className).not.toContain('ant-btn-dangerous');
    expect(button.querySelector('.anticon-pause-circle')).not.toBeNull();
  });

  it('other processes are cancelled: "Cancel", red', () => {
    renderItem(running, () => {});
    const button = screen.getByRole('button', { name: 'processes.cancel' });
    expect(button.className).toContain('ant-btn-dangerous');
    expect(button.querySelector('.anticon-close-circle')).not.toBeNull();
  });
});

describe('ProcessItem of a task factory', () => {
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });

  it('its launch opens the launch form, and the launch reaches onLaunched', async () => {
    const child = { _id: 'c1', name: 'Drop proposals' };
    mockLaunchAsync.mockReset();
    mockLaunchAsync.mockResolvedValue(child);
    const onLaunch = vi.fn();
    const onLaunched = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <ProcessItem
          process={{
            _id: 'f1', name: 'Drop proposals', status: { state: 'idle' }, progress: { percent: null },
            taskFactory: true, parameters: [{ kind: 'boolean', id: 'dry_run', label: 'Dry run' }],
          }}
          onLaunch={onLaunch}
          onLaunched={onLaunched}
        />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByRole('button'));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Dry run' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch' }));
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith(child), { timeout: 60_000 });
    expect(mockLaunchAsync).toHaveBeenCalledWith('f1', { params: { dry_run: true } });
    expect(onLaunch).not.toHaveBeenCalled();
  });
});

