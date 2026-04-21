import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import React from 'react';
import { registerWidget, _clearWidgetRegistry } from '../widgets/registry.js';

// Mock the hooks ProcessDetailView uses, BEFORE importing ProcessDetailView.
const mockProcessStream = vi.fn();
vi.mock('../hooks/useProcessStream.js', () => ({
  useProcessStream: (...args: any[]) => mockProcessStream(...args),
}));

// Mock context hooks — these live in useOptioContext.ts (imported by useProcessStream).
vi.mock('../context/useOptioContext.js', () => ({
  useOptioBaseUrl: () => 'http://host',
  useOptioPrefix: () => 'optio',
  useOptioDatabase: () => undefined,
  useOptioClient: () => undefined,
  useOptioLive: () => false,
}));

// Import after mocks.
const { ProcessDetailView } = await import('../components/ProcessDetailView.js');

describe('ProcessDetailView', () => {
  beforeEach(() => {
    _clearWidgetRegistry();
    mockProcessStream.mockReset();
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

  it('dispatches to a registered widget', () => {
    registerWidget('my-widget', (props) => (
      <div data-testid="my-widget">widget:{props.process._id}</div>
    ));
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'my-widget',
        children: [],
      },
      logs: [],
      connected: true,
    });
    render(<ProcessDetailView processId="abc" />);
    expect(screen.getByTestId('my-widget').textContent).toBe('widget:abc');
  });

  it('falls back to default when uiWidget is set but unregistered', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mockProcessStream.mockReturnValue({
      tree: {
        _id: 'abc', name: 'P', status: { state: 'running' },
        progress: { percent: null }, cancellable: true,
        depth: 0, order: 0, parentId: null,
        uiWidget: 'no-such-widget',
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
});
