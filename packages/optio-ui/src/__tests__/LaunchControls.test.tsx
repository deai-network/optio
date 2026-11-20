import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';
import type { ProcessSnapshot } from 'optio-contracts';

// A task factory's form launches through useProcessActions().launchAsync.
const mockLaunchAsync = vi.fn();
const mockDismissAsync = vi.fn();
vi.mock('../hooks/useProcessActions.js', () => ({
  useProcessActions: () => ({ launchAsync: mockLaunchAsync, dismissAsync: mockDismissAsync }),
}));

// The snapshot list LaunchControls reads (before its early returns): none unless a test gives one.
const mockUseProcessSnapshots = vi.fn<(...args: any[]) => ProcessSnapshot[] | undefined>();
vi.mock('../hooks/useProcessSnapshots.js', () => ({
  useProcessSnapshots: (...args: any[]) => mockUseProcessSnapshots(...args),
}));

import { LaunchControls } from '../components/LaunchControls.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

const TS = '2026-11-19T13:05:12.345Z';

beforeEach(() => {
  mockUseProcessSnapshots.mockReset();
  mockUseProcessSnapshots.mockReturnValue(undefined);
});

// antd's confirm modal locks body scroll and measures the scrollbar via
// getComputedStyle(el, '::-webkit-scrollbar'). jsdom ignores the
// pseudo-element and logs "Not implemented" to stderr; drop the argument
// (same result) to keep the test output clean.
function quietComputedStyle() {
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });
}

/** A click that fires a vultus action: its state updates (pending, the main half) settle in act. */
async function click(element: Element) {
  await act(async () => { fireEvent.click(element); });
}

/** Opens the split button's menu: its opener is the last button. */
function openMenu() {
  const buttons = screen.getAllByRole('button');
  fireEvent.click(buttons[buttons.length - 1]);
}

function renderWith(process: any, onLaunch = vi.fn(), onResurrect?: (processId: string) => void) {
  return {
    onLaunch,
    ...render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls process={process} onLaunch={onLaunch} onResurrect={onResurrect} size="small" />
      </I18nextProvider>,
    ),
  };
}

describe('LaunchControls', () => {
  it('renders nothing when process is in a non-launchable state', () => {
    const { container } = renderWith({ _id: '1', status: { state: 'running' } });
    expect(container.firstChild).toBeNull();
  });

  it('renders a single play button when supportsResume=false', async () => {
    const { onLaunch } = renderWith({
      _id: '1', status: { state: 'idle' }, supportsResume: false, hasSavedState: false,
    });
    const btn = screen.getByRole('button');
    await click(btn);
    expect(onLaunch).toHaveBeenCalledWith('1', undefined);
  });

  it('renders a single play button when supportsResume=true but hasSavedState=false', async () => {
    const { onLaunch } = renderWith({
      _id: '2', status: { state: 'idle' }, supportsResume: true, hasSavedState: false,
    });
    const btns = screen.getAllByRole('button');
    expect(btns.length).toBe(1);
    await click(btns[0]);
    expect(onLaunch).toHaveBeenCalledWith('2', undefined);
  });

  it('renders a split button when supportsResume=true AND hasSavedState=true', async () => {
    const { onLaunch } = renderWith({
      _id: '3', status: { state: 'idle' }, supportsResume: true, hasSavedState: true,
    });
    const primary = screen.getAllByRole('button')[0];
    await click(primary);
    expect(onLaunch).toHaveBeenCalledWith('3', { resume: true });
  });

  it('dropdown item dispatches resume=false', async () => {
    const { onLaunch } = renderWith({
      _id: '4', status: { state: 'idle' }, supportsResume: true, hasSavedState: true,
    });
    const buttons = screen.getAllByRole('button');
    const dropdownTrigger = buttons[buttons.length - 1];
    fireEvent.click(dropdownTrigger);
    const restart = await screen.findByText(/restart/i);
    await click(restart);
    expect(onLaunch).toHaveBeenCalledWith('4', { resume: false });
  });

  it('treats missing supportsResume / hasSavedState as false', async () => {
    const { onLaunch } = renderWith({ _id: '5', status: { state: 'idle' } });
    const btn = screen.getByRole('button');
    await click(btn);
    expect(onLaunch).toHaveBeenCalledWith('5', undefined);
  });

  it('renders disabled button when denyReason is set (single-button branch)', () => {
    const onLaunch = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls
          process={{ _id: '6', status: { state: 'idle' }, supportsResume: false }}
          onLaunch={onLaunch}
          size="small"
          denyReason="target disabled"
        />
      </I18nextProvider>,
    );
    const btn = screen.getByRole('button');
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(btn);
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('denyReason disables every action with its reason', async () => {
    // The list is read under the gate too: its items show, disabled with the reason.
    mockUseProcessSnapshots.mockReturnValue([
      { capturedAt: TS, endState: 'cancelled' },
      { capturedAt: '2026-11-19T12:05:00.000Z', endState: 'cancelled' },
      { capturedAt: '2026-11-19T11:05:00.000Z', endState: 'cancelled' },
    ]);
    const onLaunch = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls
          process={{ _id: '7', status: { state: 'idle' }, supportsResume: true, hasSavedState: true }}
          onLaunch={onLaunch}
          size="small"
          denyReason="not ready"
        />
      </I18nextProvider>,
    );
    expect(mockUseProcessSnapshots).toHaveBeenCalledWith('7', { enabled: true, runEndedAt: null });
    const main = screen.getAllByRole('button')[0];
    expect((main as HTMLButtonElement).disabled).toBe(true);
    await click(main);
    // Icon-only: the tooltip is the label, the reason below it.
    fireEvent.mouseEnter(main.parentElement as HTMLElement);
    expect((await screen.findByRole('tooltip', {}, { timeout: 60_000 })).textContent).toBe('Resumenot ready');
    openMenu();
    const restart = await screen.findByRole('menuitem', { name: /restart/i }, { timeout: 60_000 });
    expect(restart.getAttribute('aria-disabled')).toBe('true');
    const items = screen.getAllByRole('menuitem', { name: /Resume from previous snapshot \(.+\)$/ });
    expect(items).toHaveLength(2);
    for (const item of items) expect(item.getAttribute('aria-disabled')).toBe('true');
    await click(restart);
    await click(items[0]);
    expect(onLaunch).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('ignores empty / null denyReason — renders the usual launch affordance', async () => {
    const onLaunch = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls
          process={{ _id: '8', status: { state: 'idle' }, supportsResume: false }}
          onLaunch={onLaunch}
          size="small"
          denyReason={null}
        />
      </I18nextProvider>,
    );
    const btn = screen.getByRole('button');
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    await click(btn);
    expect(onLaunch).toHaveBeenCalledWith('8', undefined);
  });
});

describe('LaunchControls: snapshots, the warning, the icon size', () => {
  quietComputedStyle();

  const resumable = { _id: '3', status: { state: 'idle' }, supportsResume: true, hasSavedState: true };

  it('previous-snapshot items: read the list for a launchable resumable process and offer its older snapshots', async () => {
    const snapshots: ProcessSnapshot[] = [
      { capturedAt: TS, endState: 'cancelled' },
      { capturedAt: '2026-11-19T12:05:00.000Z', endState: 'cancelled' },
      { capturedAt: '2026-11-19T11:05:00.000Z', endState: 'cancelled' },
    ];
    mockUseProcessSnapshots.mockReturnValue(snapshots);
    const { onLaunch } = renderWith(resumable);
    expect(mockUseProcessSnapshots).toHaveBeenCalledWith('3', { enabled: true, runEndedAt: null });

    openMenu();
    const items = await screen.findAllByRole(
      'menuitem', { name: /Resume from previous snapshot \(.+\)$/ }, { timeout: 60_000 });
    expect(items).toHaveLength(2);
    const label = items[0].textContent!;
    await click(items[0]);
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    // The confirmation renders its title twice (the modal's header and the confirm body).
    expect(within(dialog).getAllByText(/This discards 1 newer snapshot\.$/).length).toBeGreaterThan(0);
    expect(onLaunch).not.toHaveBeenCalled();
    await click(within(dialog).getByRole('button', { name: label }));
    await waitFor(() => expect(onLaunch).toHaveBeenCalledWith(
      '3', { resume: true, resumeFrom: snapshots[1].capturedAt }), { timeout: 60_000 });
    expect(onLaunch).toHaveBeenCalledTimes(1);
  });

  // The main half's accessible name: the label of the action it shows.
  const mainHalf = () => screen.getAllByRole('button')[0].getAttribute('aria-label');
  const threeSnapshots: ProcessSnapshot[] = [
    { capturedAt: TS, endState: 'cancelled' },
    { capturedAt: '2026-11-19T12:05:00.000Z', endState: 'cancelled' },
    { capturedAt: '2026-11-19T11:05:00.000Z', endState: 'cancelled' },
  ];

  it('the main half is Resume again once a picked previous-snapshot item fired', async () => {
    mockUseProcessSnapshots.mockReturnValue(threeSnapshots);
    const { onLaunch } = renderWith(resumable);
    expect(mainHalf()).toBe('Resume');
    openMenu();
    const [item] = await screen.findAllByRole(
      'menuitem', { name: /Resume from previous snapshot \(.+\)$/ }, { timeout: 60_000 });
    const label = item.textContent!;
    await click(item);
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    await click(within(dialog).getByRole('button', { name: label }));
    await waitFor(() => expect(onLaunch).toHaveBeenCalledTimes(1), { timeout: 60_000 });
    await waitFor(() => expect(mainHalf()).toBe('Resume'), { timeout: 60_000 });
  });

  it('the main half is Resume again once a picked item\'s confirmation was cancelled', async () => {
    mockUseProcessSnapshots.mockReturnValue(threeSnapshots);
    const { onLaunch } = renderWith(resumable);
    openMenu();
    const [item] = await screen.findAllByRole(
      'menuitem', { name: /Resume from previous snapshot \(.+\)$/ }, { timeout: 60_000 });
    await click(item);
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    await click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(mainHalf()).toBe('Resume'), { timeout: 60_000 });
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('the list read is keyed by the end of the last run', () => {
    const doneAt = '2026-11-19T13:06:00.000Z';
    renderWith({ ...resumable, status: { state: 'done', doneAt } });
    expect(mockUseProcessSnapshots).toHaveBeenCalledWith('3', { enabled: true, runEndedAt: doneAt });
  });

  it('no list read for a process that is not resumable, not launchable, a factory, or without onLaunch', () => {
    const cases: Array<{ process: any; withLaunch: boolean }> = [
      { process: { _id: 'a', status: { state: 'idle' }, supportsResume: true, hasSavedState: false }, withLaunch: true },
      { process: { ...resumable, _id: 'b', status: { state: 'running' } }, withLaunch: true },
      { process: { ...resumable, _id: 'c', taskFactory: true, parameters: [] }, withLaunch: true },
      { process: { ...resumable, _id: 'd' }, withLaunch: false },
    ];
    for (const { process, withLaunch } of cases) {
      mockUseProcessSnapshots.mockClear();
      const view = render(
        <I18nextProvider i18n={i18n}>
          <LaunchControls process={process} onLaunch={withLaunch ? vi.fn() : undefined} size="small" />
        </I18nextProvider>,
      );
      expect(mockUseProcessSnapshots).toHaveBeenCalled();
      for (const call of mockUseProcessSnapshots.mock.calls) {
        expect(call).toEqual([process._id, { enabled: false, runEndedAt: null }]);
      }
      view.unmount();
    }
  });

  it("a task's warning is asked before a fresh launch, not before a resume", async () => {
    const fresh = renderWith({ _id: 'x', status: { state: 'idle' }, warning: 'Careful' });
    await click(screen.getByRole('button'));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    // antd's confirm renders its title twice (the modal's header and the confirm body).
    expect(within(dialog).getAllByText('Careful').length).toBeGreaterThan(0);
    expect(fresh.onLaunch).not.toHaveBeenCalled();
    await click(within(dialog).getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(fresh.onLaunch).toHaveBeenCalledWith('x', undefined), { timeout: 60_000 });
    expect(fresh.onLaunch).toHaveBeenCalledTimes(1);
    fresh.unmount();

    const resuming = renderWith({ ...resumable, _id: 'y', warning: 'Careful' });
    await click(screen.getAllByRole('button')[0]);
    await waitFor(() => expect(resuming.onLaunch).toHaveBeenCalledWith('y', { resume: true }), { timeout: 60_000 });
    expect(resuming.onLaunch).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it("iconFontSize sizes each action's icon, and the sized action still fires", async () => {
    const onLaunch = vi.fn();
    const { container } = render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls process={resumable} onLaunch={onLaunch} size="small" iconFontSize={20} />
      </I18nextProvider>,
    );
    const icon = container.querySelector('.anticon-play-circle') as HTMLElement;
    expect((icon.parentElement as HTMLElement).style.fontSize).toBe('20px');
    await click(screen.getAllByRole('button')[0]);
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('3', { resume: true });
  });
});

function renderResurrect(process: any) {
  const onLaunch = vi.fn();
  const onResurrect = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <LaunchControls process={process} onLaunch={onLaunch} onResurrect={onResurrect} size="small" />
    </I18nextProvider>,
  );
  return { onLaunch, onResurrect };
}

const failedWithWork = {
  _id: '9', status: { state: 'failed' },
  supportsResume: true, hasSavedState: true, supportsResurrect: true, hasUnsavedWork: true,
};

describe('LaunchControls resurrect', () => {
  // antd's confirm modal locks body scroll and measures the scrollbar via
  // getComputedStyle(el, '::-webkit-scrollbar'). jsdom ignores the
  // pseudo-element and logs "Not implemented" to stderr; drop the argument
  // (same result) to keep the test output clean.
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });

  it('primary button resurrects', async () => {
    const { onResurrect, onLaunch } = renderResurrect(failedWithWork);
    await click(screen.getByRole('button', { name: /resurrect/i }));
    expect(onResurrect).toHaveBeenCalledWith('9');
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('resume from last snapshot asks for confirmation first', async () => {
    const { onLaunch } = renderResurrect(failedWithWork);
    openMenu();
    await click(await screen.findByText(/resume from last snapshot/i, {}, { timeout: 60_000 }));
    expect(onLaunch).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    expect(within(dialog).getByText(/discards the unsaved work/i)).toBeTruthy();
    await click(within(dialog).getByRole('button', { name: 'Resume from last snapshot' }));
    await vi.waitFor(() => expect(onLaunch).toHaveBeenCalledWith('9', { resume: true }), { timeout: 60_000 });
  });

  it('restart asks for confirmation first', async () => {
    const { onLaunch } = renderResurrect(failedWithWork);
    openMenu();
    await click(await screen.findByText(/restart \(discard saved state\)/i, {}, { timeout: 60_000 }));
    expect(onLaunch).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    expect(within(dialog).getByText(/discards the unsaved work/i)).toBeTruthy();
    await click(within(dialog).getByRole('button', { name: 'Restart (discard saved state)' }));
    await vi.waitFor(() => expect(onLaunch).toHaveBeenCalledWith('9', { resume: false }), { timeout: 60_000 });
  });

  // Review Focus 4: the previous-snapshot items in the resurrect case, with a task's warning.
  it('resurrect case with older snapshots and a warning: the menu order, the confirmation, one resume', async () => {
    const snapshots: ProcessSnapshot[] = [
      { capturedAt: TS, endState: 'failed' },
      { capturedAt: '2026-11-19T12:05:00.000Z', endState: 'cancelled' },
      { capturedAt: '2026-11-19T11:05:00.000Z', endState: 'cancelled' },
    ];
    mockUseProcessSnapshots.mockReturnValue(snapshots);
    const warning = 'This wipes the data.';
    const process = { ...failedWithWork, warning };
    const { onLaunch, onResurrect } = renderResurrect(process);
    expect(mockUseProcessSnapshots).toHaveBeenCalledWith('9', { enabled: true, runEndedAt: null });
    expect(screen.getAllByRole('button')[0].getAttribute('aria-label')).toBe('Resurrect');

    openMenu();
    await screen.findByRole('menuitem', { name: /restart \(discard saved state\)/i }, { timeout: 60_000 });
    // vultus' open list lists every visible action, the main one (Resurrect) first.
    const rows = screen.getAllByRole('menuitem').map((row) => row.textContent);
    expect(rows).toHaveLength(5);
    expect(rows[0]).toBe('Resurrect');
    expect(rows[1]).toBe('Resume from last snapshot');
    expect(rows[2]).toMatch(/^Resume from previous snapshot \(.+\)$/);
    expect(rows[3]).toMatch(/^Resume from previous snapshot \(.+\)$/);
    expect(rows[4]).toBe('Restart (discard saved state)');

    await click(screen.getAllByRole('menuitem')[2]);
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    // The confirmation renders its title twice (the modal's header and the confirm body).
    expect(within(dialog).getAllByText(/This discards 1 newer snapshot\.$/).length).toBeGreaterThan(0);
    expect(dialog.textContent).toContain('What the discarded runs delivered stays in the app.');
    expect(dialog.textContent).toMatch(/discards the unsaved work/);
    expect(onLaunch).not.toHaveBeenCalled();
    await click(within(dialog).getByRole('button', { name: rows[2]! }));
    await waitFor(() => expect(onLaunch).toHaveBeenCalled(), { timeout: 60_000 });
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch).toHaveBeenCalledWith('9', { resume: true, resumeFrom: snapshots[1].capturedAt });
    expect(onResurrect).not.toHaveBeenCalled();
    // A resume asks no warning.
    expect(screen.queryByText(warning)).toBeNull();
  });

  it('resurrect case with older snapshots, while resurrecting: the one Resurrecting button', () => {
    mockUseProcessSnapshots.mockReturnValue([
      { capturedAt: TS, endState: 'failed' },
      { capturedAt: '2026-11-19T12:05:00.000Z', endState: 'cancelled' },
      { capturedAt: '2026-11-19T11:05:00.000Z', endState: 'cancelled' },
    ]);
    renderResurrect({ ...failedWithWork, warning: 'This wipes the data.', resurrecting: true });
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute('aria-label')).toMatch(/resurrecting/i);
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(true);
  });

  // F10: without onResurrect, a resurrecting process still shows the disabled Resurrecting.
  it('resurrecting without onResurrect: the disabled Resurrecting button still shows', async () => {
    const { onLaunch } = renderWith({ ...failedWithWork, resurrecting: true });
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute('aria-label')).toMatch(/resurrecting/i);
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(true);
    await click(buttons[0]);
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('no resume item without saved state', async () => {
    renderResurrect({ ...failedWithWork, hasSavedState: false });
    openMenu();
    await screen.findByText(/restart \(discard saved state\)/i, {}, { timeout: 60_000 });
    expect(screen.queryByText(/resume from last snapshot/i)).toBeNull();
  });

  it('falls back to the resume split button without onResurrect or without the flag', async () => {
    const { onLaunch } = renderWith({ ...failedWithWork });
    await click(screen.getAllByRole('button')[0]);
    expect(onLaunch).toHaveBeenCalledWith('9', { resume: true });

    const onResurrect = vi.fn();
    const flagless = renderWith({ ...failedWithWork, supportsResurrect: false }, vi.fn(), onResurrect);
    await click(within(flagless.container).getAllByRole('button')[0]);
    expect(flagless.onLaunch).toHaveBeenCalledWith('9', { resume: true });
    expect(onResurrect).not.toHaveBeenCalled();
  });

  it('while resurrecting renders a single disabled Resurrecting indicator', async () => {
    const { onLaunch, onResurrect } = renderResurrect({ ...failedWithWork, resurrecting: true });
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    const btn = screen.getByRole('button', { name: /resurrecting/i });
    expect(buttons[0]).toBe(btn);
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    await click(btn);
    expect(onResurrect).not.toHaveBeenCalled();
    expect(onLaunch).not.toHaveBeenCalled();
    fireEvent.mouseEnter(btn.parentElement as HTMLElement);
    // Icon-only: the tooltip is the label, the reason below it.
    const tip = await screen.findByRole('tooltip', {}, { timeout: 60_000 });
    expect(tip.textContent).toBe('Resurrecting…Saving the work the failed run left behind; the process resumes afterwards.');
  });

  it('the Resurrecting indicator is named through i18n (processes.resurrecting)', () => {
    const hu = i18next.createInstance();
    hu.init({
      lng: 'hu',
      resources: { hu: { translation: { processes: { resurrecting: 'Feltámasztás…' } } } },
    });
    render(
      <I18nextProvider i18n={hu}>
        <LaunchControls
          process={{ ...failedWithWork, resurrecting: true }}
          onLaunch={vi.fn()} onResurrect={vi.fn()} size="small"
        />
      </I18nextProvider>,
    );
    expect(screen.getByRole('button', { name: /Feltámasztás…/ })).toBeTruthy();
  });
});

// A task factory's record launches through its form (spec 2026-10-20-task-factory §3).
describe('LaunchControls of a task factory', () => {
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });

  const factory = {
    _id: 'f1', name: 'Drop proposals', status: { state: 'idle' }, taskFactory: true,
    warning: 'This deletes the proposals it finds.',
    parameters: [{ kind: 'text', id: 'note', label: 'Note' }],
  };

  it('the play button opens the form instead of launching, with the warning in the form', async () => {
    const { onLaunch } = renderWith(factory);
    fireEvent.click(screen.getByRole('button'));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    expect(within(dialog).getByText(/Drop proposals/)).toBeTruthy();
    expect(within(dialog).getByText(factory.warning)).toBeTruthy();
    expect(within(dialog).getByRole('textbox', { name: 'Note' })).toBeTruthy();
    expect(onLaunch).not.toHaveBeenCalled();
    // No pop-confirm of its own: the warning is the form's.
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it("the form's launch hands the launched child to onLaunched", async () => {
    const child = { _id: 'c1', name: 'Drop proposals' };
    mockLaunchAsync.mockReset();
    mockLaunchAsync.mockResolvedValue(child);
    const onLaunched = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls process={factory} onLaunch={vi.fn()} onLaunched={onLaunched} size="small" />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByRole('button'));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Note' }), { target: { value: 'hi' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch' }));
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith(child), { timeout: 60_000 });
    expect(mockLaunchAsync).toHaveBeenCalledWith('f1', { params: { note: 'hi' }, ephemeral: true });
  });

  it('renders nothing without onLaunch, as for any process', () => {
    const { container } = render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls process={factory} size="small" />
      </I18nextProvider>,
    );
    expect(container.firstChild).toBeNull();
  });

  it("a record that is not a factory's launches as before, whatever it carries", async () => {
    const { onLaunch } = renderWith({
      _id: 't1', status: { state: 'idle' }, parameters: [{ kind: 'text', id: 'note' }],
    });
    await click(screen.getByRole('button'));
    expect(onLaunch).toHaveBeenCalledWith('t1', undefined);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it.each(['done', 'failed', 'cancelled'])(
    "a factory's launch (kept by a TTL) gets no play button once %s: it is never launched again",
    (state) => {
      renderWith({
        _id: 'c1', name: 'Drop proposals', status: { state },
        metadata: { module: 'm', taskFactory: 'fac' },
      });
      expect(document.querySelector('.anticon-play-circle')).toBeNull();
    },
  );
});

// An ended launch that is kept (spec 2026-10-22-task-factory-launch-options §3.4) can be removed.
describe('LaunchControls: the remove button', () => {
  // antd's popconfirm measures the scrollbar via getComputedStyle(el, '::-webkit-scrollbar').
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });

  // The launch itself: right under its factory's record.
  const launch = {
    _id: 'c1', name: 'Drop proposals', depth: 1, parentId: 'f1', metadata: { module: 'm', taskFactory: 'fac' },
  };

  it('the remove button only on an ended launch', () => {
    mockDismissAsync.mockReset();
    const remove = () => screen.queryByRole('button', { name: 'Remove' });
    const ended = renderWith({ ...launch, status: { state: 'done' } });
    expect(remove()).not.toBeNull();
    ended.unmount();
    // A launch still running (an ephemeral one is gone once it ended, so it never shows one).
    const running = renderWith({ ...launch, status: { state: 'running' } });
    expect(remove()).toBeNull();
    running.unmount();
    const factory = renderWith({ _id: 'f1', status: { state: 'idle' }, taskFactory: true, parameters: [] });
    expect(remove()).toBeNull();
    factory.unmount();
    renderWith({ _id: 't1', status: { state: 'done' }, metadata: { module: 'm' } });
    expect(remove()).toBeNull();
  });

  it('an ended sub-task under a kept launch (its metadata inherited) shows no Remove; the launch does', () => {
    const remove = () => screen.queryByRole('button', { name: 'Remove' });
    const step = renderWith({
      ...launch, _id: 's1', name: 'Step', depth: 2, parentId: 'c1', status: { state: 'done' },
      supportsResume: true, hasSavedState: true,
    });
    expect(remove()).toBeNull();
    expect(step.container.firstChild).toBeNull();
    step.unmount();
    renderWith({ ...launch, status: { state: 'done' } });
    expect(remove()).not.toBeNull();
  });

  it('Remove asks first: Cancel keeps the launch, OK dismisses it', async () => {
    mockDismissAsync.mockReset();
    mockDismissAsync.mockResolvedValue(null);
    renderWith({ ...launch, status: { state: 'failed' } });
    const question = 'Remove this launch? Its record and log are deleted.';
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(await screen.findByText(question, {}, { timeout: 60_000 })).toBeTruthy();
    expect(mockDismissAsync).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText(question)?.closest('.ant-popover-hidden') ?? null)
      .not.toBeNull(), { timeout: 60_000 });
    expect(mockDismissAsync).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(screen.getByText(question).closest('.ant-popover-hidden')).toBeNull(), { timeout: 60_000 });
    fireEvent.click(screen.getByRole('button', { name: 'OK' }));
    await waitFor(() => expect(mockDismissAsync).toHaveBeenCalledWith('c1'), { timeout: 60_000 });
    expect(mockDismissAsync).toHaveBeenCalledTimes(1);
  });

  it('is not offered where launching is not (no onLaunch: a read-only view)', () => {
    render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls process={{ ...launch, status: { state: 'done' } }} size="small" />
      </I18nextProvider>,
    );
    expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
  });
});

// A kept launch whose task saved its state resumes (spec 2026-10-22-task-factory-launch-options §1.5).
describe('LaunchControls: resuming a kept launch', () => {
  const launch = {
    _id: 'c1', name: 'Agent run', depth: 1, parentId: 'f1', metadata: { module: 'm', taskFactory: 'fac' },
    supportsResume: true, hasSavedState: true,
  };

  it.each(['done', 'failed', 'cancelled'])(
    'a resumable kept launch shows the resume control and no play button once %s',
    (state) => {
      const { onLaunch } = renderWith({ ...launch, status: { state } });
      // The resume control a task shows, and Remove: no fresh launch, no Restart.
      expect(screen.getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['Resume', 'Remove']);
      fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
      expect(onLaunch).toHaveBeenCalledTimes(1);
      expect(onLaunch).toHaveBeenCalledWith('c1', { resume: true });
    },
  );

  it('a running one shows none', () => {
    const { container } = renderWith({ ...launch, status: { state: 'running' } });
    expect(container.firstChild).toBeNull();
  });

  it('a denyReason disables its resume, as a task\'s', () => {
    const onLaunch = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <LaunchControls
          process={{ ...launch, status: { state: 'cancelled' } }} onLaunch={onLaunch} size="small"
          denyReason="not ready"
        />
      </I18nextProvider>,
    );
    const [denied, remove] = screen.getAllByRole('button');
    expect((denied as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(denied);
    expect(onLaunch).not.toHaveBeenCalled();
    expect(remove.getAttribute('aria-label')).toBe('Remove');
  });
});
