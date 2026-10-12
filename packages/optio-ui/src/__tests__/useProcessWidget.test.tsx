import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, render, cleanup } from '@testing-library/react';
import { registerWidget, _clearWidgetRegistry } from '../widgets/registry.js';

const mockBaseUrl = vi.fn(() => 'http://host');
const mockPrefix = vi.fn(() => 'optio');
const mockDatabase = vi.fn<() => string | undefined>(() => 'mydb');

vi.mock('../context/useOptioContext.js', () => ({
  useOptioBaseUrl: () => mockBaseUrl(),
  useOptioPrefix: () => mockPrefix(),
  useOptioDatabase: () => mockDatabase(),
}));

const { useProcessWidget } = await import('../components/ProcessWidget.js');

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

describe('useProcessWidget', () => {
  beforeEach(() => {
    _clearWidgetRegistry();
    mockBaseUrl.mockReturnValue('http://host');
    mockPrefix.mockReturnValue('optio');
    mockDatabase.mockReturnValue('mydb');
  });

  afterEach(() => {
    cleanup();
  });

  it('returns mode "live" and the live component while widget-live', () => {
    const Live = () => <div data-testid="live">live</div>;
    const Suspended = () => <div data-testid="suspended">suspended</div>;
    registerWidget('my-widget', Live, { suspended: Suspended });

    const { result } = renderHook(() => useProcessWidget(baseTree({ status: { state: 'running' } })));
    expect(result.current?.mode).toBe('live');
  });

  it('returns null for live behaviour unchanged: unregistered widget warns and falls back', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = renderHook(() => useProcessWidget(baseTree({ uiWidget: 'nope' })));
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
      const { result } = renderHook(() => useProcessWidget(baseTree({ status: { state } })));
      expect(result.current).toBeNull();
    },
  );

  describe('suspended processes', () => {
    function suspendedTree(overrides: Record<string, unknown> = {}) {
      return baseTree({
        status: { state: 'cancelled' },
        supportsResume: true,
        hasSavedState: true,
        ...overrides,
      });
    }

    it('returns mode "suspended" and the suspended variant when registered', () => {
      const Live = () => <div data-testid="live">live</div>;
      const Suspended = (props: any) => <div data-testid="suspended">suspended:{props.process._id}</div>;
      registerWidget('my-widget', Live, { suspended: Suspended });

      const { result } = renderHook(() => useProcessWidget(suspendedTree()));
      expect(result.current?.mode).toBe('suspended');
    });

    it('passes the same WidgetProps shape (process, apiBaseUrl, widgetProxyUrl, prefix, database) as the live mode', () => {
      let captured: any;
      const Suspended = (props: any) => { captured = props; return null; };
      registerWidget('my-widget', () => null, { suspended: Suspended });

      function Host() {
        const result = useProcessWidget(suspendedTree());
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

    it('returns null when suspended but no suspended variant is registered (Review Focus 5)', () => {
      // Only a live component registered — e.g. the iframe widget, which has
      // no suspended variant. A suspended process with this widget falls
      // back to the default tree/log rendering, same as any other terminal
      // state today.
      registerWidget('my-widget', () => <div>live only</div>);
      const { result } = renderHook(() => useProcessWidget(suspendedTree()));
      expect(result.current).toBeNull();
    });

    it('does not warn when suspended with no variant registered (silent fallback)', () => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      registerWidget('my-widget', () => <div>live only</div>);
      renderHook(() => useProcessWidget(suspendedTree()));
      expect(warn).not.toHaveBeenCalled();
      warn.mockRestore();
    });

    it('returns null when widgetData has not arrived yet', () => {
      const Suspended = () => <div>suspended</div>;
      registerWidget('my-widget', () => null, { suspended: Suspended });
      const { result } = renderHook(() => useProcessWidget(suspendedTree({ widgetData: undefined })));
      expect(result.current).toBeNull();
    });

    it('is not suspended (and renders nothing) when cancelled without hasSavedState', () => {
      const Suspended = () => <div>suspended</div>;
      registerWidget('my-widget', () => null, { suspended: Suspended });
      const { result } = renderHook(() =>
        useProcessWidget(suspendedTree({ hasSavedState: false })));
      expect(result.current).toBeNull();
    });

    it('is not suspended (and renders nothing) when cancelled without supportsResume', () => {
      const Suspended = () => <div>suspended</div>;
      registerWidget('my-widget', () => null, { suspended: Suspended });
      const { result } = renderHook(() =>
        useProcessWidget(suspendedTree({ supportsResume: false })));
      expect(result.current).toBeNull();
    });
  });

  describe('live behaviour unchanged', () => {
    it.each(['running', 'cancel_requested', 'cancelling'])(
      'renders the live widget for widget-live state=%s',
      (state) => {
        const Live = () => <div data-testid="live">live</div>;
        registerWidget('my-widget', Live);
        const { result } = renderHook(() => useProcessWidget(baseTree({ status: { state } })));
        expect(result.current?.mode).toBe('live');
      },
    );

    it('still returns null while widgetData has not arrived', () => {
      registerWidget('my-widget', () => <div>live</div>);
      const { result } = renderHook(() => useProcessWidget(baseTree({ widgetData: undefined })));
      expect(result.current).toBeNull();
    });

    it('still warns and falls back when the database is unknown', () => {
      mockDatabase.mockReturnValue(undefined);
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      registerWidget('my-widget', () => <div>live</div>);
      const { result } = renderHook(() => useProcessWidget(baseTree()));
      expect(result.current).toBeNull();
      expect(warn).toHaveBeenCalled();
      warn.mockRestore();
    });
  });
});
