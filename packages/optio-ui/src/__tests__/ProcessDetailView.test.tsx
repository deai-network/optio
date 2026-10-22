import { describe, it, expect, afterEach, afterAll, beforeAll, vi, beforeEach } from 'vitest';
import { render, cleanup, screen, fireEvent, within, waitFor } from '@testing-library/react';
import React from 'react';
import { registerWidget, _clearWidgetRegistry } from '../widgets/registry.js';

// Mock the hooks ProcessDetailView uses, BEFORE importing ProcessDetailView.
const mockProcessStream = vi.fn();
vi.mock('../hooks/useProcessStream.js', () => ({
  useProcessStream: (...args: any[]) => mockProcessStream(...args),
}));

const mockLaunch = vi.fn();
const mockCancel = vi.fn();
const mockResurrect = vi.fn();
const mockLaunchAsync = vi.fn();
vi.mock('../hooks/useProcessActions.js', () => ({
  useProcessActions: () => ({
    launch: mockLaunch, cancel: mockCancel, resurrect: mockResurrect, launchAsync: mockLaunchAsync,
  }),
}));

// Mock context hooks — these live in useOptioContext.ts (imported by useProcessStream).
// Tests override individual return values via `mockDatabase.mockReturnValue(...)` etc.
const mockBaseUrl = vi.fn(() => 'http://host');
const mockPrefix = vi.fn(() => 'optio');
const mockDatabase = vi.fn<() => string | undefined>(() => 'mydb');

vi.mock('../context/useOptioContext.js', () => ({
  useOptioBaseUrl: () => mockBaseUrl(),
  useOptioPrefix: () => mockPrefix(),
  useOptioDatabase: () => mockDatabase(),
  useOptioClient: () => undefined,
  useOptioLive: () => false,
}));

// Import after mocks.
const { ProcessDetailView } = await import('../components/ProcessDetailView.js');
const { forgetAllProcesses } = await import('../components/lastSeenProcesses.js');

describe('ProcessDetailView', () => {
  beforeEach(() => {
    _clearWidgetRegistry();
    forgetAllProcesses();
    mockProcessStream.mockReset();
    mockLaunch.mockReset();
    mockCancel.mockReset();
    mockResurrect.mockReset();
    mockBaseUrl.mockReturnValue('http://host');
    mockPrefix.mockReturnValue('optio');
    mockDatabase.mockReturnValue('mydb');
  });

  afterEach(() => {
    cleanup();
  });

  it('shows loading when tree is null', () => {
    mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: false });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('optio-detail-loading')).toBeTruthy();
  });

  it('renders default tree+log when uiWidget is absent', () => {
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null, children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
  });

  it('dispatches to a registered widget and builds a URL with database + prefix path segments', () => {
    registerWidget('my-widget', (props) => (
      <div data-testid="my-widget" data-proxy-url={props.widgetProxyUrl}>
        widget:{props.process._id}
      </div>
    ));
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        widgetData: {},
        children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    const node = screen.getByTestId('my-widget');
    expect(node.textContent).toBe('widget:abc');
    expect(node.getAttribute('data-proxy-url'))
      .toBe('http://host/api/widget/mydb/optio/abc/');
  });

  it('URL-encodes database and prefix segments', () => {
    mockDatabase.mockReturnValue('db with spaces');
    mockPrefix.mockReturnValue('pre/fix');
    registerWidget('my-widget', (props) => (
      <div data-testid="my-widget" data-proxy-url={props.widgetProxyUrl}>w</div>
    ));
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        widgetData: {},
        children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('my-widget').getAttribute('data-proxy-url'))
      .toBe('http://host/api/widget/db%20with%20spaces/pre%2Ffix/abc/');
  });

  it('falls back to default when uiWidget is set but database is unknown', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mockDatabase.mockReturnValue(undefined);
    registerWidget('my-widget', () => <div data-testid="my-widget">should not render</div>);
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        widgetData: {},
        children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.queryByTestId('my-widget')).toBeNull();
    expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('falls back to default when uiWidget is set but unregistered', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'no-such-widget',
        widgetData: {},
        children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  // State gating — widget renders only while the task is still alive.
  it.each(['done', 'failed', 'cancelled', 'idle', 'scheduled'])(
    'falls back to default when process state is %s even if uiWidget is set',
    (state) => {
      registerWidget('my-widget', () => <div data-testid="my-widget">should not render</div>);
      mockProcessStream.mockReturnValue({
        tree: {
          _id: 'abc', name: 'P', status: { state },
          progress: { percent: null }, cancellable: true,
          depth: 0, order: 0, parentId: null,
          uiWidget: 'my-widget',
          children: [],
        },
        logs: [],
        connected: true,
      });
      render(<ProcessDetailView processId="abc" />);
      expect(screen.queryByTestId('my-widget')).toBeNull();
      expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
    },
  );

  it.each(['cancel_requested', 'cancelling'])(
    'keeps the widget while state is %s (task still alive, lax gating)',
    (state) => {
      registerWidget('my-widget', () => <div data-testid="my-widget">live</div>);
      mockProcessStream.mockReturnValue({
        tree: {
          _id: 'abc', name: 'P', status: { state },
          progress: { percent: null }, cancellable: true,
          depth: 0, order: 0, parentId: null,
          uiWidget: 'my-widget',
          widgetData: {},
          children: [],
        },
        logs: [],
        connected: true,
      });
      render(<ProcessDetailView processId="abc" />);
      expect(screen.getByTestId('my-widget')).toBeTruthy();
    },
  );

  it('renders the log panel above the widget in widget mode', () => {
    registerWidget('my-widget', () => <div data-testid="my-widget">widget body</div>);
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        widgetData: {},
        children: [],
      },
      logs: [
        { timestamp: '2026-04-22T10:00:00Z', level: 'event', message: 'State changed to running' },
      ],
      connected: true,
    });
    const { container } = render(<ProcessDetailView processId="abc" />);
    const layout = container.querySelector('[data-testid="optio-widget-layout"]');
    expect(layout).not.toBeNull();
    // Log panel first (top), widget second (bottom).
    const children = Array.from(layout!.children);
    expect(children.length).toBeGreaterThanOrEqual(2);
    const widget = screen.getByTestId('my-widget');
    const widgetIndex = children.findIndex((c) => c.contains(widget));
    expect(widgetIndex).toBeGreaterThan(0);
    // The log entry is visible.
    expect(screen.getByText('State changed to running')).toBeTruthy();
  });

  it('hides the log panel and renders only the widget when showLogs is false', () => {
    registerWidget('my-widget', () => <div data-testid="my-widget">widget body</div>);
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        widgetData: {},
        children: [],
      },
      logs: [
        { timestamp: '2026-04-22T10:00:00Z', level: 'event', message: 'State changed to running' },
      ],
      connected: true,
    });
    const { container } = render(<ProcessDetailView processId="abc" showLogs={false} />);
    // Widget still renders...
    expect(screen.getByTestId('my-widget')).toBeTruthy();
    // ...but the log strip is gone: the layout has only the widget wrapper, and
    // the log entry is not in the DOM.
    const layout = container.querySelector('[data-testid="optio-widget-layout"]');
    expect(layout!.children.length).toBe(1);
    expect(screen.queryByText('State changed to running')).toBeNull();
  });

  it('renders the LaunchControls play icon for a launchable process by default', () => {
    // 'done' is in LAUNCHABLE_STATES (idle/done/failed/cancelled), so
    // LaunchControls produces a play button when onLaunch is provided.
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'done' },
        progress: { percent: null }, cancellable: false,
        depth: 0, order: 0, parentId: null, children: [],
      },
      logs: [],
      connected: true,
    });
    const { container } = render(<ProcessDetailView processId="abc" />);
    expect(container.querySelector('.anticon-play-circle')).not.toBeNull();
  });

  it('hides the LaunchControls when readOnly is true', () => {
    // Same launchable process, but the embed is read-only — no actions
    // should appear (today: launch; future actions are gated by the
    // same flag).
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'done' },
        progress: { percent: null }, cancellable: false,
        depth: 0, order: 0, parentId: null, children: [],
      },
      logs: [],
      connected: true,
    });
    const { container } = render(<ProcessDetailView processId="abc" readOnly />);
    expect(container.querySelector('.anticon-play-circle')).toBeNull();
  });

  it('renders the cancel button for a cancellable running process by default', () => {
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null, children: [],
      },
      logs: [],
      connected: true,
    });
    const { container } = render(<ProcessDetailView processId="abc" />);
    expect(container.querySelector('.anticon-close-circle')).not.toBeNull();
  });

  it('hides the cancel button when readOnly is true', () => {
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null, children: [],
      },
      logs: [],
      connected: true,
    });
    const { container } = render(<ProcessDetailView processId="abc" readOnly />);
    expect(container.querySelector('.anticon-close-circle')).toBeNull();
  });

  it('falls back to default tree+log rendering while widgetData is not yet set', () => {
    // Covers the period between process launch and the worker's
    // set_widget_data call (e.g. optio-opencode uploading its binary).
    // During that window the iframe widget would just show its "Loading…"
    // placeholder and hide the process progress/log; falling through to
    // the default layout keeps progress + logs visible until widgetData
    // arrives.
    registerWidget('my-widget', () => <div data-testid="my-widget">should not render yet</div>);
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: 25, message: 'Uploading opencode binary: 25%' },
        cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        // widgetData intentionally omitted (undefined)
        children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.queryByTestId('my-widget')).toBeNull();
    expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
  });

  describe('suspended processes', () => {
    function suspendedTree(overrides: Record<string, unknown> = {}) {
      return {
        _id: 'abc', name: 'P', status: { state: 'cancelled' },
        supportsResume: true, hasSavedState: true,
        progress: { percent: null }, cancellable: false,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        widgetData: {},
        children: [],
        ...overrides,
      };
    }

    it('shows the suspended widget variant and a Resume control', () => {
      registerWidget(
        'my-widget',
        () => <div data-testid="my-widget">live</div>,
        { suspended: (props) => <div data-testid="suspended-widget">suspended:{props.process._id}</div> },
      );
      mockProcessStream.mockReturnValue({ tree: suspendedTree(), logs: [], connected: true });
      const { container } = render(<ProcessDetailView processId="abc" />);
      expect(screen.getByTestId('suspended-widget').textContent).toBe('suspended:abc');
      expect(screen.queryByTestId('my-widget')).toBeNull();
      // Resume control (LaunchControls primary button for a resumable process).
      expect(container.querySelector('.anticon-play-circle')).not.toBeNull();
    });

    it('Resume calls launch with resume: true', () => {
      registerWidget(
        'my-widget',
        () => <div data-testid="my-widget">live</div>,
        { suspended: () => <div data-testid="suspended-widget">suspended</div> },
      );
      mockProcessStream.mockReturnValue({ tree: suspendedTree(), logs: [], connected: true });
      const { container } = render(<ProcessDetailView processId="abc" />);
      const button = container.querySelector('.anticon-play-circle')!.closest('button')!;
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      expect(mockLaunch).toHaveBeenCalledWith('abc', { resume: true });
    });

    it('hides the Resume control when readOnly is true', () => {
      registerWidget(
        'my-widget',
        () => <div data-testid="my-widget">live</div>,
        { suspended: () => <div data-testid="suspended-widget">suspended</div> },
      );
      mockProcessStream.mockReturnValue({ tree: suspendedTree(), logs: [], connected: true });
      const { container } = render(<ProcessDetailView processId="abc" readOnly />);
      expect(screen.getByTestId('suspended-widget')).toBeTruthy();
      expect(screen.queryByTestId('optio-widget-suspended-actions')).toBeNull();
      expect(container.querySelector('.anticon-play-circle')).toBeNull();
    });

    it('passes readOnly to the widget (so it cannot resume the process itself)', () => {
      const seen: unknown[] = [];
      registerWidget('my-widget', () => null, {
        suspended: (props) => { seen.push(props.readOnly); return <div data-testid="suspended-widget" />; },
      });
      mockProcessStream.mockReturnValue({ tree: suspendedTree(), logs: [], connected: true });
      const { unmount } = render(<ProcessDetailView processId="abc" readOnly />);
      expect(seen.at(-1)).toBe(true);
      unmount();
      render(<ProcessDetailView processId="abc" />);
      expect(seen.at(-1)).toBeUndefined();
    });

    it('falls back to default tree+log when suspended but no suspended variant is registered', () => {
      registerWidget('my-widget', () => <div data-testid="my-widget">live</div>);
      mockProcessStream.mockReturnValue({ tree: suspendedTree(), logs: [], connected: true });
      render(<ProcessDetailView processId="abc" />);
      expect(screen.queryByTestId('my-widget')).toBeNull();
      expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
    });

    // A process whose last capture failed keeps hasUnsavedWork while an older
    // snapshot leaves hasSavedState set, so it is suspended AND resurrectable.
    // Both layouts must offer Resurrect, and Resume must ask before it
    // discards the host's unsaved work.
    describe.each([
      ['suspended widget layout', true, 'optio-widget-layout'],
      ['default layout', false, 'optio-detail-default'],
    ])('suspended with unsaved work, %s', (_label, withSuspendedVariant, layoutTestId) => {
      // antd's confirm modal measures the scrollbar via
      // getComputedStyle(el, '::-webkit-scrollbar'); jsdom logs "Not
      // implemented" for the pseudo-element, so drop the argument.
      beforeAll(() => {
        const real = globalThis.getComputedStyle.bind(globalThis);
        vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
      });
      afterAll(() => {
        vi.restoreAllMocks();
      });

      function renderUnsaved() {
        if (withSuspendedVariant) {
          registerWidget(
            'my-widget',
            () => <div data-testid="my-widget">live</div>,
            { suspended: () => <div data-testid="suspended-widget">suspended</div> },
          );
        } else {
          registerWidget('my-widget', () => <div data-testid="my-widget">live</div>);
        }
        mockProcessStream.mockReturnValue({
          tree: suspendedTree({ supportsResurrect: true, hasUnsavedWork: true }),
          logs: [],
          connected: true,
        });
        render(<ProcessDetailView processId="abc" />);
        expect(screen.getByTestId(layoutTestId)).toBeTruthy();
      }

      it('primary button resurrects', () => {
        renderUnsaved();
        fireEvent.click(screen.getByRole('button', { name: /resurrect/i }));
        expect(mockResurrect).toHaveBeenCalledWith('abc');
        expect(mockLaunch).not.toHaveBeenCalled();
      });

      it('Resume from last snapshot asks to discard the unsaved work first', async () => {
        renderUnsaved();
        // The menu trigger is the Resurrect button's sibling in the split button.
        const split = screen.getByRole('button', { name: /resurrect/i }).parentElement!;
        const splitButtons = within(split).getAllByRole('button');
        fireEvent.click(splitButtons[splitButtons.length - 1]);
        fireEvent.click(await screen.findByText(/resume from last snapshot/i, {}, { timeout: 60_000 }));
        expect(mockLaunch).not.toHaveBeenCalled();
        expect(await screen.findByText(/discards the unsaved work/i, {}, { timeout: 60_000 })).toBeTruthy();
        fireEvent.click(await screen.findByRole('button', { name: /discard and continue/i }, { timeout: 60_000 }));
        await vi.waitFor(() => expect(mockLaunch).toHaveBeenCalledWith('abc', { resume: true }), { timeout: 60_000 });
        expect(mockResurrect).not.toHaveBeenCalled();
      });
    });

    it('live behaviour is unchanged when the process is actually live (not suspended)', () => {
      registerWidget(
        'my-widget',
        () => <div data-testid="my-widget">live</div>,
        { suspended: () => <div data-testid="suspended-widget">suspended</div> },
      );
      mockProcessStream.mockReturnValue({
        tree: suspendedTree({ status: { state: 'running' } }),
        logs: [],
        connected: true,
      });
      render(<ProcessDetailView processId="abc" />);
      expect(screen.getByTestId('my-widget')).toBeTruthy();
      expect(screen.queryByTestId('suspended-widget')).toBeNull();
    });
  });

  // waitForWidget / widgetPlaceholder (owner request 2026-10-12): an embed
  // that only wants the process's widget hides the phase before it is
  // available, the process view, behind a big spinner or its own placeholder.
  describe('waiting for the widget', () => {
    const tree = (state: string, widgetData: unknown = null) => ({
      _id: 'abc', name: 'P', status: { state },
      progress: { percent: null }, cancellable: true,
      depth: 0, order: 0, parentId: null, children: [],
      uiWidget: 'my-widget', widgetData,
    });
    const stream = (t: unknown) => mockProcessStream.mockReturnValue({ tree: t, logs: [], connected: true });
    beforeEach(() => {
      registerWidget('my-widget', () => <div data-testid="my-widget" />);
    });

    it('shows the big spinner instead of the loading line before the process arrives', () => {
      mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: false });
      render(<ProcessDetailView processId="abc" waitForWidget />);
      expect(screen.getByTestId('optio-detail-waiting')).toBeTruthy();
      expect(screen.queryByTestId('optio-detail-loading')).toBeNull();
    });

    it('shows it instead of the process view while the process is scheduled or runs without its widget', () => {
      for (const state of ['scheduled', 'running']) {
        stream(tree(state));
        render(<ProcessDetailView processId="abc" waitForWidget />);
        expect(screen.getByTestId('optio-detail-waiting')).toBeTruthy();
        expect(screen.queryByTestId('optio-detail-default')).toBeNull();
        cleanup();
      }
    });

    it('shows the widget once it is available', () => {
      stream(tree('running', {}));
      render(<ProcessDetailView processId="abc" waitForWidget showLogs={false} />);
      expect(screen.getByTestId('my-widget')).toBeTruthy();
      expect(screen.queryByTestId('optio-detail-waiting')).toBeNull();
    });

    it('shows a placeholder instead of the spinner, which needs no flag of its own', () => {
      stream(tree('running'));
      render(<ProcessDetailView processId="abc" widgetPlaceholder={<div data-testid="placeholder">Starting…</div>} />);
      expect(within(screen.getByTestId('optio-detail-waiting')).getByTestId('placeholder')).toBeTruthy();
      expect(screen.queryByTestId('optio-detail-default')).toBeNull();
    });

    it('falls back to the process view once the process ended without its widget', () => {
      for (const state of ['failed', 'done']) {
        stream(tree(state));
        render(<ProcessDetailView processId="abc" waitForWidget />);
        expect(screen.getByTestId('optio-detail-default')).toBeTruthy();
        expect(screen.queryByTestId('optio-detail-waiting')).toBeNull();
        cleanup();
      }
    });

    it('still says when the process is missing or cannot be read', () => {
      mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: true, processNotFound: true });
      render(<ProcessDetailView processId="abc" waitForWidget />);
      expect(screen.getByTestId('optio-detail-not-found')).toBeTruthy();
      cleanup();
      mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: false, error: new Error('boom') });
      render(<ProcessDetailView processId="abc" waitForWidget />);
      expect(screen.getByTestId('optio-detail-error')).toBeTruthy();
    });
  });

  // A task factory's record launches through its form (spec 2026-10-20-task-factory §3).
  describe('a task factory', () => {
    beforeAll(() => {
      const real = globalThis.getComputedStyle.bind(globalThis);
      vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((elt) => real(elt));
    });
    afterAll(() => {
      vi.restoreAllMocks();
    });

    const factoryTree = {
      _id: 'f1', name: 'Drop proposals', status: { state: 'idle' },
      progress: { percent: null }, cancellable: true,
      depth: 0, order: 0, parentId: null, children: [],
      taskFactory: true,
      parameters: [{ kind: 'numeric', id: 'limit', label: 'Limit', min: 1, max: 9, initialValue: 3 }],
    };

    it('its launch opens the launch form, which launches with the values and reports the child', async () => {
      const child = { _id: 'c1', name: 'Drop proposals' };
      mockLaunchAsync.mockReset();
      mockLaunchAsync.mockResolvedValue(child);
      const onLaunched = vi.fn();
      mockProcessStream.mockReturnValue({ tree: factoryTree, logs: [], connected: true });
      const { container } = render(<ProcessDetailView processId="f1" onLaunched={onLaunched} />);
      const play = container.querySelector('.anticon-play-circle')!.closest('button')!;
      fireEvent.click(play);
      const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
      expect(mockLaunch).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Launch' }));
      await waitFor(() => expect(onLaunched).toHaveBeenCalledWith(child), { timeout: 60_000 });
      expect(mockLaunchAsync).toHaveBeenCalledWith('f1', { params: { limit: 3 }, ephemeral: true });
      expect(mockLaunch).not.toHaveBeenCalled();
    });

    it("launchOverrides reach the tree's play button, by the factory's processId", async () => {
      mockProcessStream.mockReturnValue({ tree: { ...factoryTree, processId: 'app.drop' }, logs: [], connected: true });
      const { container } = render(
        <ProcessDetailView processId="f1" launchOverrides={{ 'app.drop': { limit: { visible: false } } }} />,
      );
      fireEvent.click(container.querySelector('.anticon-play-circle')!.closest('button')!);
      const dialog = await screen.findByRole('dialog', {}, { timeout: 60_000 });
      // antd keeps a hidden item (its value is still sent), out of sight.
      expect(within(dialog).getByText('Limit').closest('.ant-form-item')!.classList.contains('ant-form-item-hidden')).toBe(true);
    });

    it('read-only, it offers no launch', () => {
      mockProcessStream.mockReturnValue({ tree: factoryTree, logs: [], connected: true });
      const { container } = render(<ProcessDetailView processId="f1" readOnly />);
      expect(container.querySelector('.anticon-play-circle')).toBeNull();
    });
  });

  // A process deleted while open keeps its last state on screen (spec 2026-10-22 §3.5).
  describe('a deleted process', () => {
    const runningTree = (id: string) => ({
      _id: id, name: `Process ${id}`, status: { state: 'running' },
      progress: { percent: null }, cancellable: true,
      depth: 0, order: 0, parentId: null, children: [],
    });
    const showThenDelete = (id: string) => {
      mockProcessStream.mockReturnValue({
        tree: runningTree(id),
        logs: [{ timestamp: '2026-04-22T10:00:00Z', level: 'info', message: `last words of ${id}` }],
        connected: true,
      });
      const view = render(<ProcessDetailView processId={id} />);
      mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: false, processNotFound: true });
      view.rerender(<ProcessDetailView processId={id} />);
      return view;
    };
    const show = (id: string) => {
      mockProcessStream.mockReturnValue({ tree: runningTree(id), logs: [], connected: true });
      render(<ProcessDetailView processId={id} />);
      cleanup();
    };
    const lookUp = (id: string) => {
      mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: false, processNotFound: true });
      render(<ProcessDetailView processId={id} />);
      const found = screen.queryByTestId('optio-detail-not-found') === null;
      cleanup();
      return found;
    };

    it('shows its last tree read-only and its last log under the deleted notice', () => {
      const { container } = showThenDelete('p1');
      expect(screen.getByText('Process deleted.')).toBeTruthy();
      expect(screen.queryByTestId('optio-detail-not-found')).toBeNull();
      expect(screen.getByText('Process p1')).toBeTruthy();
      expect(screen.getByText('last words of p1')).toBeTruthy();
      expect(container.querySelector('.anticon-close-circle')).toBeNull();
      expect(container.querySelector('.anticon-play-circle')).toBeNull();
      expect(container.querySelector('.anticon-delete')).toBeNull();
    });

    it('says not found for a process it never showed', () => {
      mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: false, processNotFound: true });
      render(<ProcessDetailView processId="never" />);
      expect(screen.getByTestId('optio-detail-not-found').textContent).toBe('Process not found.');
      expect(screen.queryByText('Process deleted.')).toBeNull();
    });

    it('remembers the last five processes shown, the least recently shown dropped', () => {
      for (const id of ['p1', 'p2', 'p3', 'p4', 'p5', 'p6']) show(id);
      expect(lookUp('p1')).toBe(false);
      for (const id of ['p2', 'p3', 'p4', 'p5', 'p6']) expect(lookUp(id)).toBe(true);
    });

    it('moves a process shown again to the front', () => {
      for (const id of ['p1', 'p2', 'p3', 'p4', 'p5']) show(id);
      show('p1');
      show('p6');
      expect(lookUp('p2')).toBe(false);
      expect(lookUp('p1')).toBe(true);
    });
  });

});
