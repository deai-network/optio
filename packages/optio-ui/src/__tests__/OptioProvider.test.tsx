import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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
