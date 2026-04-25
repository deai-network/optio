import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import React from 'react';
import { registerWidget, _clearWidgetRegistry } from '../widgets/registry.js';

// Mock the hooks ProcessDetailView uses, BEFORE importing ProcessDetailView.
const mockProcessStream = vi.fn();
vi.mock('../hooks/useProcessStream.js', () => ({
  useProcessStream: (...args: any[]) => mockProcessStream(...args),
}));

const mockLaunch = vi.fn();
vi.mock('../hooks/useProcessActions.js', () => ({
  useProcessActions: () => ({ launch: mockLaunch }),
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

describe('ProcessDetailView', () => {
  beforeEach(() => {
    _clearWidgetRegistry();
    mockProcessStream.mockReset();
    mockLaunch.mockReset();
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

  it('renders LaunchControls in the header when tree is loaded', () => {
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'idle' },
        progress: { percent: null }, cancellable: false,
        depth: 0, order: 0, parentId: null, children: [],
      },
      logs: [],
      connected: false,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('optio-detail-header')).toBeTruthy();
  });
});
