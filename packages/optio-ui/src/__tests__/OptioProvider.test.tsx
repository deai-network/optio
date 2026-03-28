import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { OptioProvider } from '../context/OptioProvider.js';
import { useOptioPrefix } from '../context/useOptioContext.js';

// Mock the discovery hook
let mockDiscoveryResult = { prefix: null as string | null, prefixes: [] as string[], isLoading: false };

vi.mock('../hooks/usePrefixDiscovery.js', () => ({
  usePrefixDiscovery: () => mockDiscoveryResult,
}));

function PrefixDisplay() {
  const prefix = useOptioPrefix();
  return <div data-testid="prefix">{prefix}</div>;
}

function renderWithProvider(props: { prefix?: string }) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <OptioProvider {...props}>
        <PrefixDisplay />
      </OptioProvider>
    </QueryClientProvider>,
  );
}

describe('OptioProvider prefix resolution', () => {
  it('uses explicit prefix when provided', () => {
    mockDiscoveryResult = { prefix: 'discovered', prefixes: ['discovered'], isLoading: false };
    renderWithProvider({ prefix: 'explicit' });
    expect(screen.getByTestId('prefix').textContent).toBe('explicit');
  });

  it('uses discovered prefix when no explicit prefix given', () => {
    mockDiscoveryResult = { prefix: 'discovered', prefixes: ['discovered'], isLoading: false };
    renderWithProvider({});
    expect(screen.getByTestId('prefix').textContent).toBe('discovered');
  });

  it('falls back to optio when no explicit prefix and discovery returns null', () => {
    mockDiscoveryResult = { prefix: null, prefixes: [], isLoading: false };
    renderWithProvider({});
    expect(screen.getByTestId('prefix').textContent).toBe('optio');
  });
});
