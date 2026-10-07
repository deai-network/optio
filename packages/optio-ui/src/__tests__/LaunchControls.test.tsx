import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';

import { LaunchControls } from '../components/LaunchControls.js';

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

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

  it('renders a single play button when supportsResume=false', () => {
    const { onLaunch } = renderWith({
      _id: '1', status: { state: 'idle' }, supportsResume: false, hasSavedState: false,
    });
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(onLaunch).toHaveBeenCalledWith('1', undefined);
  });

  it('renders a single play button when supportsResume=true but hasSavedState=false', () => {
    const { onLaunch } = renderWith({
      _id: '2', status: { state: 'idle' }, supportsResume: true, hasSavedState: false,
    });
    const btns = screen.getAllByRole('button');
    expect(btns.length).toBe(1);
    fireEvent.click(btns[0]);
    expect(onLaunch).toHaveBeenCalledWith('2', undefined);
  });

  it('renders a split button when supportsResume=true AND hasSavedState=true', () => {
    const { onLaunch } = renderWith({
      _id: '3', status: { state: 'idle' }, supportsResume: true, hasSavedState: true,
    });
    const primary = screen.getAllByRole('button')[0];
    fireEvent.click(primary);
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
    fireEvent.click(restart);
    expect(onLaunch).toHaveBeenCalledWith('4', { resume: false });
  });

  it('treats missing supportsResume / hasSavedState as false', () => {
    const { onLaunch } = renderWith({ _id: '5', status: { state: 'idle' } });
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
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

  it('renders disabled button when denyReason is set (split-button branch suppressed)', () => {
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
    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBe(1);
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it('ignores empty / null denyReason — renders the usual launch affordance', () => {
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
    fireEvent.click(btn);
    expect(onLaunch).toHaveBeenCalledWith('8', undefined);
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

  it('primary button resurrects', () => {
    const { onResurrect, onLaunch } = renderResurrect(failedWithWork);
    fireEvent.click(screen.getByRole('button', { name: /resurrect/i }));
    expect(onResurrect).toHaveBeenCalledWith('9');
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('resume from last snapshot asks for confirmation first', async () => {
    const { onLaunch } = renderResurrect(failedWithWork);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    fireEvent.click(await screen.findByText(/resume from last snapshot/i, {}, { timeout: 60_000 }));
    expect(onLaunch).not.toHaveBeenCalled();
    expect(await screen.findByText(/discards the unsaved work/i, {}, { timeout: 60_000 })).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: /discard and continue/i }, { timeout: 60_000 }));
    await vi.waitFor(() => expect(onLaunch).toHaveBeenCalledWith('9', { resume: true }), { timeout: 60_000 });
  });

  it('restart asks for confirmation first', async () => {
    const { onLaunch } = renderResurrect(failedWithWork);
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    fireEvent.click(await screen.findByText(/^restart$/i, {}, { timeout: 60_000 }));
    fireEvent.click(await screen.findByRole('button', { name: /discard and continue/i }, { timeout: 60_000 }));
    await vi.waitFor(() => expect(onLaunch).toHaveBeenCalledWith('9', { resume: false }), { timeout: 60_000 });
  });

  it('no resume item without saved state', async () => {
    renderResurrect({ ...failedWithWork, hasSavedState: false });
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    await screen.findByText(/^restart$/i, {}, { timeout: 60_000 });
    expect(screen.queryByText(/resume from last snapshot/i)).toBeNull();
  });

  it('falls back to the resume split button without onResurrect or without the flag', () => {
    const { onLaunch } = renderWith({ ...failedWithWork });
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(onLaunch).toHaveBeenCalledWith('9', { resume: true });

    const onResurrect = vi.fn();
    const flagless = renderWith({ ...failedWithWork, supportsResurrect: false }, vi.fn(), onResurrect);
    fireEvent.click(within(flagless.container).getAllByRole('button')[0]);
    expect(flagless.onLaunch).toHaveBeenCalledWith('9', { resume: true });
    expect(onResurrect).not.toHaveBeenCalled();
  });

  it('while resurrecting renders a single disabled Resurrecting indicator', async () => {
    const { onLaunch, onResurrect } = renderResurrect({ ...failedWithWork, resurrecting: true });
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    const btn = screen.getByRole('button', { name: 'Resurrecting' });
    expect(buttons[0]).toBe(btn);
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(btn);
    expect(onResurrect).not.toHaveBeenCalled();
    expect(onLaunch).not.toHaveBeenCalled();
    fireEvent.mouseEnter(btn.parentElement as HTMLElement);
    const tip = await screen.findByRole('tooltip', {}, { timeout: 60_000 });
    expect(tip.textContent).toBe('Resurrecting: saving the unsaved work…');
  });

  it('the Resurrecting indicator is named through i18n (processes.resurrectingLabel)', () => {
    const hu = i18next.createInstance();
    hu.init({
      lng: 'hu',
      resources: { hu: { translation: { processes: { resurrectingLabel: 'Feltámasztás' } } } },
    });
    render(
      <I18nextProvider i18n={hu}>
        <LaunchControls
          process={{ ...failedWithWork, resurrecting: true }}
          onLaunch={vi.fn()} onResurrect={vi.fn()} size="small"
        />
      </I18nextProvider>,
    );
    expect(screen.getByRole('button', { name: 'Feltámasztás' })).toBeTruthy();
  });
});
