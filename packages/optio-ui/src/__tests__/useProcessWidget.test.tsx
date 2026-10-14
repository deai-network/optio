import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { isValidElement, useEffect, type ReactNode } from 'react';
import { act, renderHook, render, screen, cleanup } from '@testing-library/react';
import { registerWidget, _clearWidgetRegistry } from '../widgets/registry.js';

const mockBaseUrl = vi.fn(() => 'http://host');
const mockPrefix = vi.fn(() => 'optio');
const mockDatabase = vi.fn<() => string | undefined>(() => 'mydb');

vi.mock('../context/useOptioContext.js', () => ({
  useOptioBaseUrl: () => mockBaseUrl(),
  useOptioPrefix: () => mockPrefix(),
  useOptioDatabase: () => mockDatabase(),
}));

const { useProcessWidget, useProcessWidgetState } = await import('../components/ProcessWidget.js');

function baseTree(overrides: Record<string, unknown> = {}) {
  return {
    _id: 'abc',
    name: 'P',
    status: { state: 'running' },
    progress: { percent: null },
    cancellable: true,
    depth: 0,
    order: 0,
    parentId: null,
    children: [],
    uiWidget: 'my-widget',
    widgetData: {},
    ...overrides,
  } as any;
}

function suspendedTree(overrides: Record<string, unknown> = {}) {
  return baseTree({
    status: { state: 'cancelled' },
    supportsResume: true,
    hasSavedState: true,
    ...overrides,
  });
}

beforeEach(() => {
  _clearWidgetRegistry();
  mockBaseUrl.mockReturnValue('http://host');
  mockPrefix.mockReturnValue('optio');
  mockDatabase.mockReturnValue('mydb');
});

afterEach(() => {
  cleanup();
});

// `useProcessWidgetState` carries the full gating logic (live vs. suspended,
// registry lookup, proxy-URL build) plus the `mode` discriminator that lets
// ProcessDetailView decide whether to show the Resume control. These tests
// exercise that logic directly.
describe('useProcessWidgetState', () => {
  it('returns mode "live" and the live component while widget-live', () => {
    const Live = () => <div data-testid="live">live</div>;
    const Suspended = () => <div data-testid="suspended">suspended</div>;
    registerWidget('my-widget', Live, { suspended: Suspended });

    const { result } = renderHook(() => useProcessWidgetState(baseTree({ status: { state: 'running' } })));
    expect(result.current?.mode).toBe('live');
  });

  it('returns null for live behaviour unchanged: unregistered widget warns and falls back', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = renderHook(() => useProcessWidgetState(baseTree({ uiWidget: 'nope' })));
    expect(result.current).toBeNull();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it.each(['idle', 'done', 'failed', 'scheduled'])(
    'returns null for non-suspended, non-live state=%s',
    (state) => {
      const Live = () => <div>live</div>;
      const Suspended = () => <div>suspended</div>;
      registerWidget('my-widget', Live, { suspended: Suspended });
      const { result } = renderHook(() => useProcessWidgetState(baseTree({ status: { state } })));
      expect(result.current).toBeNull();
    },
  );

  describe('suspended processes', () => {
    it('returns mode "suspended" and the suspended variant when registered', () => {
      const Live = () => <div data-testid="live">live</div>;
      const Suspended = (props: any) => <div data-testid="suspended">suspended:{props.process._id}</div>;
      registerWidget('my-widget', Live, { suspended: Suspended });

      const { result } = renderHook(() => useProcessWidgetState(suspendedTree()));
      expect(result.current?.mode).toBe('suspended');
    });

    it('passes the same WidgetProps shape (process, apiBaseUrl, widgetProxyUrl, prefix, database) as the live mode', () => {
      let captured: any;
      const Suspended = (props: any) => { captured = props; return null; };
      registerWidget('my-widget', () => null, { suspended: Suspended });

      function Host() {
        const result = useProcessWidgetState(suspendedTree());
        return <>{result?.widget}</>;
      }
      render(<Host />);
      expect(captured).toMatchObject({
        apiBaseUrl: 'http://host',
        widgetProxyUrl: 'http://host/api/widget/mydb/optio/abc/',
        prefix: 'optio',
        database: 'mydb',
      });
      expect(captured.process._id).toBe('abc');
    });

    it('adds readOnly to WidgetProps only when the caller asks for it', () => {
      let captured: any;
      const Suspended = (props: any) => { captured = props; return null; };
      registerWidget('my-widget', () => null, { suspended: Suspended });
      function Host({ readOnly }: { readOnly?: boolean }) {
        return <>{useProcessWidget(suspendedTree(), readOnly ? { readOnly } : undefined)}</>;
      }
      const { rerender } = render(<Host />);
      expect('readOnly' in captured).toBe(false);
      rerender(<Host readOnly />);
      expect(captured.readOnly).toBe(true);
    });

    it('adds hideEmptyRuns to WidgetProps only when the caller asks for it, live and suspended alike', () => {
      const captured: Record<string, any> = {};
      registerWidget('my-widget', (props: any) => { captured.live = props; return null; }, {
        suspended: (props: any) => { captured.suspended = props; return null; },
      });
      function Host({ tree, hide }: { tree: any; hide?: boolean }) {
        return <>{useProcessWidget(tree, hide ? { hideEmptyRuns: true } : undefined)}</>;
      }
      const { rerender } = render(<Host tree={baseTree()} />);
      expect('hideEmptyRuns' in captured.live).toBe(false);
      rerender(<Host tree={baseTree()} hide />);
      expect(captured.live.hideEmptyRuns).toBe(true);
      render(<Host tree={suspendedTree({ _id: 'def' })} hide />);
      expect(captured.suspended.hideEmptyRuns).toBe(true);
    });

    it('returns null when suspended but no suspended variant is registered (Review Focus 5)', () => {
      // Only a live component registered — e.g. the iframe widget, which has
      // no suspended variant. A suspended process with this widget falls
      // back to the default tree/log rendering, same as any other terminal
      // state today.
      registerWidget('my-widget', () => <div>live only</div>);
      const { result } = renderHook(() => useProcessWidgetState(suspendedTree()));
      expect(result.current).toBeNull();
    });

    it('does not warn when suspended with no variant registered (silent fallback)', () => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      registerWidget('my-widget', () => <div>live only</div>);
      renderHook(() => useProcessWidgetState(suspendedTree()));
      expect(warn).not.toHaveBeenCalled();
      warn.mockRestore();
    });

    it('returns null when widgetData has not arrived yet', () => {
      const Suspended = () => <div>suspended</div>;
      registerWidget('my-widget', () => null, { suspended: Suspended });
      const { result } = renderHook(() => useProcessWidgetState(suspendedTree({ widgetData: undefined })));
      expect(result.current).toBeNull();
    });

    it('is not suspended (and renders nothing) when cancelled without hasSavedState', () => {
      const Suspended = () => <div>suspended</div>;
      registerWidget('my-widget', () => null, { suspended: Suspended });
      const { result } = renderHook(() =>
        useProcessWidgetState(suspendedTree({ hasSavedState: false })));
      expect(result.current).toBeNull();
    });

    it('is not suspended (and renders nothing) when cancelled without supportsResume', () => {
      const Suspended = () => <div>suspended</div>;
      registerWidget('my-widget', () => null, { suspended: Suspended });
      const { result } = renderHook(() =>
        useProcessWidgetState(suspendedTree({ supportsResume: false })));
      expect(result.current).toBeNull();
    });
  });

  describe('live behaviour unchanged', () => {
    it.each(['running', 'cancel_requested', 'cancelling'])(
      'renders the live widget for widget-live state=%s',
      (state) => {
        const Live = () => <div data-testid="live">live</div>;
        registerWidget('my-widget', Live);
        const { result } = renderHook(() => useProcessWidgetState(baseTree({ status: { state } })));
        expect(result.current?.mode).toBe('live');
      },
    );

    it('still returns null while widgetData has not arrived', () => {
      registerWidget('my-widget', () => <div>live</div>);
      const { result } = renderHook(() => useProcessWidgetState(baseTree({ widgetData: undefined })));
      expect(result.current).toBeNull();
    });

    it('still warns and falls back when the database is unknown', () => {
      mockDatabase.mockReturnValue(undefined);
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      registerWidget('my-widget', () => <div>live</div>);
      const { result } = renderHook(() => useProcessWidgetState(baseTree()));
      expect(result.current).toBeNull();
      expect(warn).toHaveBeenCalled();
      warn.mockRestore();
    });
  });
});

// `useProcessWidget` is the pre-existing, backward-compatible entry point:
// callers that render its result directly (e.g. Excavator's
// ProcessDetailPage: `const widget = useProcessWidget(tree); ...{widget}`)
// must keep getting a plain renderable node, never a `{ widget, mode }`
// object — that would throw "Objects are not valid as a React child" and
// break their types. It now also returns the suspended variant, with zero
// change required on the caller's side.
describe('useProcessWidget (backward-compatible: ReactNode | null, not an object)', () => {
  it('returns a renderable element directly (not a {widget, mode} object) in live mode', () => {
    const Live = () => <div data-testid="live">live</div>;
    registerWidget('my-widget', Live);

    const { result } = renderHook(() => useProcessWidget(baseTree()));
    expect(isValidElement(result.current)).toBe(true);
    expect(result.current).not.toHaveProperty('mode');
  });

  it('returns a renderable element directly (not a {widget, mode} object) in suspended mode', () => {
    const Suspended = () => <div data-testid="suspended">suspended</div>;
    registerWidget('my-widget', () => null, { suspended: Suspended });

    const { result } = renderHook(() => useProcessWidget(suspendedTree()));
    expect(isValidElement(result.current)).toBe(true);
    expect(result.current).not.toHaveProperty('mode');
  });

  it('renders the live widget directly when rendered by a host component', () => {
    registerWidget('my-widget', () => <div data-testid="live">live content</div>);
    function Host() {
      return <>{useProcessWidget(baseTree())}</>;
    }
    render(<Host />);
    expect(screen.getByTestId('live')).toBeTruthy();
  });

  it('renders the suspended widget directly when rendered by a host component (Excavator-style call site)', () => {
    registerWidget('my-widget', () => <div data-testid="live">live content</div>, {
      suspended: () => <div data-testid="suspended">suspended content</div>,
    });
    function Host() {
      // Mirrors Excavator's own ProcessDetailPage: `const widget =
      // useProcessWidget(tree)`, then rendering `widget` directly.
      const widget = useProcessWidget(suspendedTree());
      return <>{widget}</>;
    }
    render(<Host />);
    expect(screen.getByTestId('suspended')).toBeTruthy();
    expect(screen.queryByTestId('live')).toBeNull();
  });

  it('returns null (not an object) when nothing should render', () => {
    registerWidget('my-widget', () => <div>live</div>);
    const { result } = renderHook(() => useProcessWidget(baseTree({ status: { state: 'done' } })));
    expect(result.current).toBeNull();
  });

  it('returns null when suspended with no suspended variant registered', () => {
    registerWidget('my-widget', () => <div>live only</div>);
    const { result } = renderHook(() => useProcessWidget(suspendedTree()));
    expect(result.current).toBeNull();
  });
});

describe('useProcessWidgetState: a resume keeps the suspended variant on screen', () => {
  // A relaunch clears widgetData until the task sets it again: without this,
  // the hook returns null for those seconds and the host's layout flips.
  const Live = () => <div data-testid="live" />;
  const Suspended = () => <div data-testid="suspended" />;
  const resumed = (overrides: Record<string, unknown> = {}) =>
    baseTree({ supportsResume: true, hasSavedState: true, widgetData: null, ...overrides });
  const hook = (tree: any) => renderHook(({ t }) => useProcessWidgetState(t), { initialProps: { t: tree } });

  beforeEach(() => registerWidget('my-widget', Live, { suspended: Suspended }));

  it('scheduled, then running without widgetData: still the suspended variant, mode "resuming"', () => {
    const h = hook(suspendedTree());
    expect(h.result.current?.mode).toBe('suspended');
    h.rerender({ t: resumed({ status: { state: 'scheduled' } }) });
    expect(h.result.current?.mode).toBe('resuming');
    h.rerender({ t: resumed() });
    expect(h.result.current?.mode).toBe('resuming');
    render(<>{h.result.current?.widget}</>);
    expect(screen.getByTestId('suspended')).toBeTruthy();
  });

  it('hands over to the live widget once it is ready, and the latch is then spent', () => {
    let onReady: (() => void) | undefined;
    _clearWidgetRegistry();
    registerWidget('my-widget', (props: any) => { onReady = props.onReady; return <div data-testid="live" />; },
      { suspended: Suspended });
    let mode: string | null = null;
    function Host({ t }: { t: any }) {
      const result = useProcessWidgetState(t);
      mode = result?.mode ?? null;
      return <>{result?.widget}</>;
    }
    const { rerender } = render(<Host t={suspendedTree()} />);
    rerender(<Host t={resumed()} />);
    rerender(<Host t={resumed({ widgetData: { protocol: 'claudecode' } })} />);
    expect(mode).toBe('resuming');
    act(() => onReady!());
    expect(mode).toBe('live');
    // The latch is spent: a later gap shows nothing, as before.
    rerender(<Host t={resumed()} />);
    expect(mode).toBeNull();
  });

  it('a run that ends before the live widget is ready falls back to nothing', () => {
    const h = hook(suspendedTree());
    h.rerender({ t: resumed() });
    h.rerender({ t: resumed({ status: { state: 'failed' } }) });
    expect(h.result.current).toBeNull();
  });

  it('without having shown the suspended variant first, a starting run shows nothing', () => {
    const h = hook(resumed());
    expect(h.result.current).toBeNull();
  });

  it('only for the process it showed', () => {
    const h = hook(suspendedTree());
    h.rerender({ t: resumed({ _id: 'other' }) });
    expect(h.result.current).toBeNull();
  });

  it('no latch when the suspended variant was never rendered (none registered)', () => {
    _clearWidgetRegistry();
    registerWidget('my-widget', Live);
    const h = hook(suspendedTree());
    expect(h.result.current).toBeNull();
    h.rerender({ t: resumed() });
    expect(h.result.current).toBeNull();
  });
});

describe('useProcessWidgetState: a placeholder while the widget is on its way', () => {
  // With `placeholder`, an active process that names a registered widget but
  // has none to show yet gets the placeholder (mode "waiting"), and widgets
  // receive it as `WidgetProps.placeholder` for their own loading states.
  const Live = (props: any) => <div data-testid="live">{props.placeholder}</div>;
  const Suspended = (props: any) => <div data-testid="suspended">{props.placeholder}</div>;
  const card = <div data-testid="card" />;
  const starting = (overrides: Record<string, unknown> = {}) => baseTree({ widgetData: null, ...overrides });

  beforeEach(() => registerWidget('my-widget', Live, { suspended: Suspended }));

  it.each(['scheduled', 'running'])('returns the placeholder as mode "waiting" while %s without widgetData', (state) => {
    const { result } = renderHook(() => useProcessWidgetState(starting({ status: { state } }), { placeholder: card }));
    expect(result.current?.mode).toBe('waiting');
    render(<>{result.current?.widget}</>);
    expect(screen.getByTestId('card')).toBeTruthy();
    expect(screen.queryByTestId('live')).toBeNull();
  });

  it('without a placeholder, a starting process still shows nothing', () => {
    const { result } = renderHook(() => useProcessWidgetState(starting()));
    expect(result.current).toBeNull();
  });

  it.each(['idle', 'done', 'failed'])('returns null for state=%s even with a placeholder', (state) => {
    const { result } = renderHook(() => useProcessWidgetState(starting({ status: { state } }), { placeholder: card }));
    expect(result.current).toBeNull();
  });

  it('returns null with a placeholder when no live widget is registered under the name', () => {
    const { result } = renderHook(() => useProcessWidgetState(starting({ uiWidget: 'nope' }), { placeholder: card }));
    expect(result.current).toBeNull();
  });

  it('a resume keeps the suspended variant rather than the placeholder', () => {
    const h = renderHook(({ t }) => useProcessWidgetState(t, { placeholder: card }), { initialProps: { t: suspendedTree() as any } });
    h.rerender({ t: baseTree({ supportsResume: true, hasSavedState: true, widgetData: null }) });
    expect(h.result.current?.mode).toBe('resuming');
  });

  it('hands the placeholder to the widget, live and suspended alike', () => {
    const live = renderHook(() => useProcessWidgetState(baseTree(), { placeholder: card }));
    expect(live.result.current?.mode).toBe('live');
    render(<>{live.result.current?.widget}</>);
    expect(screen.getByTestId('live').querySelector('[data-testid="card"]')).toBeTruthy();
    cleanup();
    const suspended = renderHook(() => useProcessWidgetState(suspendedTree(), { placeholder: card }));
    render(<>{suspended.result.current?.widget}</>);
    expect(screen.getByTestId('suspended').querySelector('[data-testid="card"]')).toBeTruthy();
  });

  it('adds placeholder to WidgetProps only when the caller gives one', () => {
    let captured: any;
    _clearWidgetRegistry();
    registerWidget('my-widget', () => null, { suspended: (props: any) => { captured = props; return null; } });
    function Host({ withCard }: { withCard?: boolean }) {
      return <>{useProcessWidget(suspendedTree(), withCard ? { placeholder: card } : undefined)}</>;
    }
    const { rerender } = render(<Host />);
    expect('placeholder' in captured).toBe(false);
    rerender(<Host withCard />);
    expect(captured.placeholder).toBe(card);
  });
});

describe('useProcessWidgetState: the resume handover', () => {
  // A resumed process's live widget mounts hidden beside the suspended variant
  // and takes over on WidgetProps.onReady, or after the 10 s safety net
  // (docs/2026-10-14-conversation-backlog-batch-design.md, section 4).
  let liveMounts = 0;
  let liveOnReady: (() => void) | undefined;
  let lastMode: string | null = null;
  const Live = (props: any) => {
    useEffect(() => { liveMounts += 1; }, []);
    liveOnReady = props.onReady;
    return <div data-testid="live" />;
  };
  const Suspended = () => <div data-testid="suspended" />;
  const resumedLive = (overrides: Record<string, unknown> = {}) =>
    baseTree({ supportsResume: true, hasSavedState: true, widgetData: { protocol: 'claudecode' }, ...overrides });
  function Host({ t, placeholder }: { t: any; placeholder?: ReactNode }) {
    const result = useProcessWidgetState(t, placeholder !== undefined ? { placeholder } : undefined);
    lastMode = result?.mode ?? null;
    return <>{result?.widget}</>;
  }
  const slotOf = (testId: string) => screen.getByTestId(testId).parentElement as HTMLElement;
  function handover(placeholder?: ReactNode) {
    const view = render(<Host t={suspendedTree()} placeholder={placeholder} />);
    view.rerender(<Host t={resumedLive()} placeholder={placeholder} />);
    return view;
  }

  beforeEach(() => {
    liveMounts = 0;
    liveOnReady = undefined;
    lastMode = null;
    registerWidget('my-widget', Live, { suspended: Suspended });
  });
  afterEach(() => { vi.useRealTimers(); });

  it('mounts the live widget hidden next to the shown suspended variant', () => {
    handover();
    expect(lastMode).toBe('resuming');
    expect(slotOf('suspended').style.display).toBe('contents');
    expect(slotOf('live').style.display).toBe('none');
    expect(slotOf('live').hasAttribute('inert')).toBe(true);
    expect(typeof liveOnReady).toBe('function');
  });

  it('onReady shows the live widget without remounting it and drops the suspended one', () => {
    handover();
    act(() => liveOnReady!());
    expect(lastMode).toBe('live');
    expect(screen.queryByTestId('suspended')).toBeNull();
    expect(slotOf('live').style.display).toBe('contents');
    expect(slotOf('live').hasAttribute('inert')).toBe(false);
    expect(liveMounts).toBe(1);
  });

  it('shows the live widget after 10 s without onReady', () => {
    vi.useFakeTimers();
    handover();
    act(() => { vi.advanceTimersByTime(9_999); });
    expect(lastMode).toBe('resuming');
    act(() => { vi.advanceTimersByTime(1); });
    expect(lastMode).toBe('live');
    expect(screen.queryByTestId('suspended')).toBeNull();
    expect(liveMounts).toBe(1);
  });

  it('a late onReady changes nothing, and the next resume waits for its own', () => {
    const view = handover();
    const first = liveOnReady!;
    act(() => first());
    act(() => first());
    expect(lastMode).toBe('live');
    view.rerender(<Host t={suspendedTree()} />);
    expect(lastMode).toBe('suspended');
    view.rerender(<Host t={resumedLive()} />);
    expect(lastMode).toBe('resuming');
    act(() => first());
    expect(lastMode).toBe('resuming');
    expect(slotOf('live').style.display).toBe('none');
  });

  it('suspended again during the handover: the suspended variant, no hidden live widget', () => {
    const view = handover();
    view.rerender(<Host t={suspendedTree()} />);
    expect(lastMode).toBe('suspended');
    expect(screen.getByTestId('suspended')).toBeTruthy();
    expect(screen.queryByTestId('live')).toBeNull();
  });

  it('failed during the handover: nothing', () => {
    vi.useFakeTimers();
    const view = handover();
    view.rerender(<Host t={resumedLive({ status: { state: 'failed' } })} />);
    expect(lastMode).toBeNull();
    expect(screen.queryByTestId('live')).toBeNull();
    expect(screen.queryByTestId('suspended')).toBeNull();
    act(() => { vi.advanceTimersByTime(10_000); });
    expect(lastMode).toBeNull();
  });

  it('a new placeholder element during the handover does not remount the live widget', () => {
    const view = handover(<div data-testid="card-1" />);
    view.rerender(<Host t={resumedLive()} placeholder={<div data-testid="card-2" />} />);
    expect(liveMounts).toBe(1);
    act(() => liveOnReady!());
    expect(lastMode).toBe('live');
    expect(liveMounts).toBe(1);
  });

  it('gives onReady only during a handover', () => {
    render(<Host t={baseTree()} />);
    expect(lastMode).toBe('live');
    expect(liveOnReady).toBeUndefined();
    expect(slotOf('live').style.display).toBe('contents');
  });
});
