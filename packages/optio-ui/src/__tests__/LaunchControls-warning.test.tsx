import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';
import type { ProcessSnapshot } from 'optio-contracts';

vi.mock('../hooks/useProcessActions.js', () => ({
  useProcessActions: () => ({ launchAsync: vi.fn(), dismissAsync: vi.fn() }),
}));

// The snapshot list LaunchControls reads: none unless a test gives one.
const mockUseProcessSnapshots = vi.fn<(...args: any[]) => ProcessSnapshot[] | undefined>();
vi.mock('../hooks/useProcessSnapshots.js', () => ({
  useProcessSnapshots: (...args: any[]) => mockUseProcessSnapshots(...args),
}));

import { LaunchControls } from '../components/LaunchControls.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

const WARNING = 'This wipes the data and loads it again from scratch.';
const TS = '2026-11-19T13:05:12.345Z';

beforeEach(() => {
  mockUseProcessSnapshots.mockReset();
  mockUseProcessSnapshots.mockReturnValue(undefined);
});

// antd's confirm modal measures the scrollbar via getComputedStyle(el, '::-webkit-scrollbar');
// jsdom logs "Not implemented" for the pseudo-element, so drop the argument (same result).
beforeAll(() => {
  const real = globalThis.getComputedStyle.bind(globalThis);
  vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
});
afterAll(() => {
  vi.restoreAllMocks();
});

function renderWith(process: any, onResurrect?: (id: string) => void) {
  const onLaunch = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <LaunchControls process={process} onLaunch={onLaunch} onResurrect={onResurrect} size="small" />
    </I18nextProvider>,
  );
  return onLaunch;
}

/** Opens the split button's menu (its opener is the last button) and picks the item. */
async function pick(name: RegExp) {
  const buttons = screen.getAllByRole('button');
  fireEvent.click(buttons[buttons.length - 1]);
  fireEvent.click(await screen.findByRole('menuitem', { name }, { timeout: 60_000 }));
}

/** The open dialog that shows `text` (antd's confirm renders its title twice: header and body). */
async function dialogWith(text: string | RegExp) {
  await screen.findAllByText(text, {}, { timeout: 60_000 });
  return screen.getAllByRole('dialog').find((d) => within(d).queryAllByText(text).length > 0)!;
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
    expect(await dialogWith(WARNING)).toBeTruthy();
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('OK launches once', async () => {
    const onLaunch = renderWith(process);
    clickPlay();
    await dialogWith(WARNING);
    fireEvent.click(screen.getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(onLaunch).toHaveBeenCalled());
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('1', undefined);
  });

  it('Cancel launches nothing', async () => {
    const onLaunch = renderWith(process);
    clickPlay();
    await dialogWith(WARNING);
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

// The warning describes what a fresh run does: Restart (a fresh run) asks it, after its own
// confirmation if it has one; a resume (Resume, a previous-snapshot item, Resurrect) does not.
describe('LaunchControls: the warning of a resumable task', () => {
  const resumable = {
    _id: 'r', status: { state: 'failed' }, warning: WARNING, supportsResume: true, hasSavedState: true,
  };
  const resurrectable = { ...resumable, supportsResurrect: true, hasUnsavedWork: true };

  it('Restart asks the warning; OK launches once, a fresh run', async () => {
    const onLaunch = renderWith(resumable);
    await pick(/restart \(discard saved state\)/i);
    const dialog = await dialogWith(WARNING);
    expect(onLaunch).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(onLaunch).toHaveBeenCalled(), { timeout: 60_000 });
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('r', { resume: false });
  });

  it('Restart in the resurrect case asks the warning after its discard confirmation; OK launches once', async () => {
    const onLaunch = renderWith(resurrectable, vi.fn());
    await pick(/restart \(discard saved state\)/i);
    const discard = await dialogWith(/discards the unsaved work/i);
    expect(screen.queryByText(WARNING)).toBeNull();
    fireEvent.click(within(discard).getByRole('button', { name: 'Restart (discard saved state)' }));
    const dialog = await dialogWith(WARNING);
    expect(onLaunch).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(onLaunch).toHaveBeenCalled(), { timeout: 60_000 });
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('r', { resume: false });
  });

  it('Restart: Cancel on the warning launches nothing', async () => {
    const onLaunch = renderWith(resumable);
    await pick(/restart \(discard saved state\)/i);
    const dialog = await dialogWith(WARNING);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('Resume does not ask it', async () => {
    const onLaunch = renderWith(resumable);
    fireEvent.click(screen.getAllByRole('button')[0]);
    await waitFor(() => expect(onLaunch).toHaveBeenCalled(), { timeout: 60_000 });
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('r', { resume: true });
    expect(screen.queryByText(WARNING)).toBeNull();
  });

  it('a previous-snapshot item does not ask it: its own confirmation, then the resume', async () => {
    const older = '2026-11-19T12:05:00.000Z';
    mockUseProcessSnapshots.mockReturnValue([
      { capturedAt: TS, endState: 'failed' }, { capturedAt: older, endState: 'cancelled' },
    ]);
    const onLaunch = renderWith(resumable);
    await pick(/Resume from previous snapshot/);
    const dialog = await dialogWith(/This discards 1 newer snapshot\.$/);
    fireEvent.click(within(dialog).getByRole('button', { name: /Resume from previous snapshot/ }));
    await waitFor(() => expect(onLaunch).toHaveBeenCalled(), { timeout: 60_000 });
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('r', { resume: true, resumeFrom: older });
    expect(screen.queryByText(WARNING)).toBeNull();
  });

  it('Resurrect does not ask it', async () => {
    const onResurrect = vi.fn();
    const onLaunch = renderWith(resurrectable, onResurrect);
    fireEvent.click(screen.getByRole('button', { name: /^resurrect$/i }));
    await waitFor(() => expect(onResurrect).toHaveBeenCalled(), { timeout: 60_000 });
    expect(onResurrect).toHaveBeenCalledTimes(1);
    expect(onResurrect).toHaveBeenCalledWith('r');
    expect(onLaunch).not.toHaveBeenCalled();
    expect(screen.queryByText(WARNING)).toBeNull();
  });
});
