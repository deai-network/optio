import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';
import type { Process } from 'optio-contracts';

const mockLaunchAsync = vi.fn();
vi.mock('../hooks/useProcessActions.js', () => ({
  useProcessActions: () => ({ launchAsync: mockLaunchAsync }),
}));

const mockUseProcess = vi.fn();
vi.mock('../hooks/useProcessQueries.js', () => ({
  useProcess: (id: string | undefined) => mockUseProcess(id),
}));

import { useTaskFactory } from '../hooks/useTaskFactory.js';
import { TaskFactoryLaunchButton } from '../components/TaskFactoryLaunchButton.js';
import { ProcessItem } from '../components/ProcessItem.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

const factory = {
  _id: 'f1', processId: 'app.drop', name: 'Drop proposals', status: { state: 'idle' }, progress: { percent: null },
  taskFactory: true,
  parameters: [{ kind: 'boolean', id: 'dry_run', label: 'Dry run' }],
};

function Probe({ onLaunched }: { onLaunched?: (child: Process) => void }) {
  const { launch, isPending, dialog } = useTaskFactory('app.drop', { onLaunched });
  return (
    <>
      <button disabled={launch.disabled} title={launch.reason} onClick={() => launch.fire()}>open</button>
      <span data-testid="pending">{String(isPending)}</span>
      {dialog}
    </>
  );
}

function renderProbe(onLaunched?: (child: Process) => void) {
  return render(
    <I18nextProvider i18n={i18n}>
      <Probe onLaunched={onLaunched} />
    </I18nextProvider>,
  );
}

describe('useTaskFactory', () => {
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });
  beforeEach(() => {
    mockLaunchAsync.mockReset();
    mockUseProcess.mockReset();
  });

  it('reads the record by the id it is given', () => {
    mockUseProcess.mockReturnValue({ process: factory, isLoading: false });
    renderProbe();
    expect(mockUseProcess).toHaveBeenCalledWith('app.drop');
  });

  it('is disabled, with a reason, while the record loads', () => {
    mockUseProcess.mockReturnValue({ process: null, isLoading: true });
    renderProbe();
    const open = screen.getByRole('button', { name: 'open' }) as HTMLButtonElement;
    expect(open.disabled).toBe(true);
    expect(open.title).toBe('Loading…');
  });

  it('is disabled, with a reason, for a record that is no factory', () => {
    mockUseProcess.mockReturnValue({ process: { ...factory, taskFactory: false }, isLoading: false });
    renderProbe();
    const open = screen.getByRole('button', { name: 'open' }) as HTMLButtonElement;
    expect(open.disabled).toBe(true);
    expect(open.title).toBe('Not a task factory');
  });

  it('is disabled for a process that was not found', () => {
    mockUseProcess.mockReturnValue({ process: null, isLoading: false });
    renderProbe();
    expect((screen.getByRole('button', { name: 'open' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('firing opens the dialog; a submit is pending until the launch answers; onLaunched gets the child', async () => {
    mockUseProcess.mockReturnValue({ process: factory, isLoading: false });
    const child = { _id: 'c1', name: 'Drop proposals' };
    let answer!: (c: unknown) => void;
    mockLaunchAsync.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    const onLaunched = vi.fn();
    renderProbe(onLaunched);
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'open' }));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    expect(screen.getByTestId('pending').textContent).toBe('false');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch' }));
    await waitFor(() => expect(screen.getByTestId('pending').textContent).toBe('true'), { timeout: 60_000 });
    expect((screen.getByRole('button', { name: 'open', hidden: true }) as HTMLButtonElement).disabled).toBe(true);
    expect(onLaunched).not.toHaveBeenCalled();
    answer(child);
    await waitFor(() => expect(onLaunched).toHaveBeenCalledWith(child), { timeout: 60_000 });
    await waitFor(() => expect(screen.getByTestId('pending').textContent).toBe('false'), { timeout: 60_000 });
    expect(mockLaunchAsync).toHaveBeenCalledWith('f1', { params: { dry_run: false }, ephemeral: true });
  });

  it('is not pending after a refused launch', async () => {
    mockUseProcess.mockReturnValue({ process: factory, isLoading: false });
    mockLaunchAsync.mockRejectedValue({ status: 409, body: { reason: 'launch-blocked' } });
    renderProbe();
    fireEvent.click(screen.getByRole('button', { name: 'open' }));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Launch' }));
    await waitFor(() => expect(mockLaunchAsync).toHaveBeenCalled(), { timeout: 60_000 });
    await waitFor(() => expect(screen.getByTestId('pending').textContent).toBe('false'), { timeout: 60_000 });
  });
});

describe('TaskFactoryLaunchButton', () => {
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });

  it('is a button that opens the launch form, with the overrides applied', async () => {
    mockUseProcess.mockReturnValue({ process: factory, isLoading: false });
    render(
      <I18nextProvider i18n={i18n}>
        <TaskFactoryLaunchButton factoryId="app.drop" overrides={{ dry_run: { visible: false } }} />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: /Launch/ }));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    expect(within(dialog).queryByRole('switch', { name: 'Dry run' })).toBeNull();
    expect(within(dialog).getByText('Remove when finished')).toBeTruthy();
  });
});

describe('launchOverrides', () => {
  beforeAll(() => {
    const real = globalThis.getComputedStyle.bind(globalThis);
    vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
  });
  afterAll(() => {
    vi.restoreAllMocks();
  });

  it("a ProcessItem gives its factory's form the entry under the factory's processId", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <ProcessItem
          process={factory as any}
          onLaunch={vi.fn()}
          launchOverrides={{ [factory.processId]: { dry_run: { visible: false } } }}
        />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByRole('button'));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    expect(within(dialog).queryByRole('switch', { name: 'Dry run' })).toBeNull();
  });

  it('without an entry for the factory, the form is whole', async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <ProcessItem
          process={factory as any}
          onLaunch={vi.fn()}
          launchOverrides={{ 'someone.else': { dry_run: { visible: false } } }}
        />
      </I18nextProvider>,
    );
    fireEvent.click(screen.getByRole('button'));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
    expect(within(dialog).getByRole('switch', { name: 'Dry run' })).toBeTruthy();
  });
});
