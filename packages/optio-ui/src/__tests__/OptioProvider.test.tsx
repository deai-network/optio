import { describe, it, expect, vi } from 'vitest';
import { useEffect } from 'react';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MessageSinkContext, useMessageSink } from 'vultus-core';
import { useOptioPrefix, useOptioDatabase, useOptioLive } from '../context/useOptioContext.js';

let mockDiscoveryResult = {
  instance: null as { database: string; prefix: string; live: boolean } | null,
  instances: [] as { database: string; prefix: string; live: boolean }[],
  isLoading: false,
};

vi.mock('../hooks/useInstanceDiscovery.js', () => ({
  useInstanceDiscovery: () => mockDiscoveryResult,
}));

vi.mock('../client.js', () => ({
  createOptioClient: () => ({}),
}));

const { OptioProvider } = await import('../context/OptioProvider.js');

function ContextDisplay() {
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  const live = useOptioLive();
  return (
    <>
      <div data-testid="prefix">{prefix}</div>
      <div data-testid="database">{database ?? 'undefined'}</div>
      <div data-testid="live">{String(live)}</div>
    </>
  );
}

function renderWithProvider(props: { prefix?: string; database?: string; live?: boolean }) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <OptioProvider {...props}>
        <ContextDisplay />
      </OptioProvider>
    </QueryClientProvider>,
  );
}

describe('OptioProvider resolution', () => {
  it('uses explicit prefix and database when provided', () => {
    mockDiscoveryResult = {
      instance: { database: 'discovered-db', prefix: 'discovered', live: false },
      instances: [{ database: 'discovered-db', prefix: 'discovered', live: false }],
      isLoading: false,
    };
    renderWithProvider({ prefix: 'explicit', database: 'explicit-db', live: true });
    expect(screen.getByTestId('prefix').textContent).toBe('explicit');
    expect(screen.getByTestId('database').textContent).toBe('explicit-db');
    expect(screen.getByTestId('live').textContent).toBe('true');
  });

  it('uses discovered instance when no explicit values given', () => {
    mockDiscoveryResult = {
      instance: { database: 'auto-db', prefix: 'auto', live: true },
      instances: [{ database: 'auto-db', prefix: 'auto', live: true }],
      isLoading: false,
    };
    renderWithProvider({});
    expect(screen.getByTestId('prefix').textContent).toBe('auto');
    expect(screen.getByTestId('database').textContent).toBe('auto-db');
    expect(screen.getByTestId('live').textContent).toBe('true');
  });

  it('falls back to optio when no explicit values and discovery returns null', () => {
    mockDiscoveryResult = { instance: null, instances: [], isLoading: false };
    renderWithProvider({});
    expect(screen.getByTestId('prefix').textContent).toBe('optio');
    expect(screen.getByTestId('database').textContent).toBe('undefined');
    expect(screen.getByTestId('live').textContent).toBe('false');
  });
});

describe('OptioProvider browser-open fallback notification', () => {
  it('shows the popup-blocked fallback through antd notification under the app ConfigProvider', async () => {
    const { ConfigProvider } = await import('antd');
    const { waitFor } = await import('@testing-library/react');
    const { handleBrowserOpenRequests, __resetBrowserOpenSeenForTest } = await import('../handlers/browserOpen.js');
    __resetBrowserOpenSeenForTest();
    const open = vi.spyOn(window, 'open').mockReturnValue(null); // popup blocked
    mockDiscoveryResult = { instance: null, instances: [], isLoading: false };
    render(
      <ConfigProvider prefixCls="vx">
        <QueryClientProvider client={new QueryClient()}>
          <OptioProvider prefix="p" database="d">
            <div />
          </OptioProvider>
        </QueryClientProvider>
      </ConfigProvider>,
    );
    handleBrowserOpenRequests([{ requestId: 'nb1', url: 'https://example.test', createdAt: Date.now() }]);
    await waitFor(() => {
      expect(document.body.querySelector('.vx-notification')?.textContent).toContain('A task wants to open a page');
    });
    open.mockRestore();
  });
});

// For an app without a vultus-antd VultusProvider (optio-dashboard): where vultus-core's messages
// under the provider go, e.g. a launch refusal useLaunchShowingRefusals shows.
describe('OptioProvider messageSink', () => {
  /** Sends `text` to the MessageSink it finds, once. */
  function Say({ text }: { text: string }) {
    const sink = useMessageSink();
    useEffect(() => { sink(text, 'error'); }, [sink, text]);
    return null;
  }

  it('is the MessageSink of everything under it', () => {
    mockDiscoveryResult = { instance: null, instances: [], isLoading: false };
    const sink = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OptioProvider prefix="p" database="d" messageSink={sink}>
          <Say text="refused" />
        </OptioProvider>
      </QueryClientProvider>,
    );
    expect(sink).toHaveBeenCalledWith('refused', 'error');
  });

  it('left out, the enclosing sink stays (a VultusProvider\'s)', () => {
    mockDiscoveryResult = { instance: null, instances: [], isLoading: false };
    const outer = vi.fn();
    render(
      <MessageSinkContext.Provider value={outer}>
        <QueryClientProvider client={new QueryClient()}>
          <OptioProvider prefix="p" database="d">
            <Say text="refused" />
          </OptioProvider>
        </QueryClientProvider>
      </MessageSinkContext.Provider>,
    );
    expect(outer).toHaveBeenCalledWith('refused', 'error');
  });
});
