import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  handleBrowserOpenRequests,
  __resetBrowserOpenSeenForTest,
  isBrowserOpenStale,
  BROWSER_OPEN_MAX_AGE_MS,
} from '../handlers/browserOpen.js';

const fresh = () => new Date().toISOString();
const old = () => new Date(Date.now() - BROWSER_OPEN_MAX_AGE_MS - 5_000).toISOString();

describe('isBrowserOpenStale', () => {
  it('fresh timestamp is not stale; old one is', () => {
    expect(isBrowserOpenStale(fresh())).toBe(false);
    expect(isBrowserOpenStale(old())).toBe(true);
  });
  it('missing or unparseable timestamp is stale', () => {
    expect(isBrowserOpenStale(undefined)).toBe(true);
    expect(isBrowserOpenStale('not-a-date')).toBe(true);
  });
  it('accepts an epoch-ms number', () => {
    expect(isBrowserOpenStale(Date.now())).toBe(false);
    expect(isBrowserOpenStale(Date.now() - BROWSER_OPEN_MAX_AGE_MS - 1)).toBe(true);
  });
});

describe('handleBrowserOpenRequests', () => {
  beforeEach(() => {
    __resetBrowserOpenSeenForTest();
    vi.restoreAllMocks();
  });

  it('opens each url exactly once across repeated deliveries (dedup by requestId)', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    const reqs = [{ requestId: 'r1', url: 'https://x', createdAt: fresh() }];
    handleBrowserOpenRequests(reqs);
    handleBrowserOpenRequests(reqs); // re-delivered on the next poll tick
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith('https://x', '_blank', 'noopener,noreferrer');
  });

  it('strips surrounding quotes from a captured url', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    handleBrowserOpenRequests([{ requestId: 'r2', url: '"https://q"', createdAt: fresh() }]);
    expect(open).toHaveBeenCalledWith('https://q', '_blank', 'noopener,noreferrer');
  });

  it('ignores stale requests replayed from history (e.g. after a page reload)', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    handleBrowserOpenRequests([{ requestId: 'old', url: 'https://old', createdAt: old() }]);
    handleBrowserOpenRequests([{ requestId: 'none', url: 'https://none' }]); // no timestamp
    expect(open).not.toHaveBeenCalled();
  });

  it('is a no-op for empty/undefined input', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    handleBrowserOpenRequests(undefined);
    handleBrowserOpenRequests([]);
    expect(open).not.toHaveBeenCalled();
  });

  it('dedups across distinct feed chokepoints (shared module-level Set)', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    const t = fresh();
    handleBrowserOpenRequests([{ requestId: 'shared', url: 'https://z', createdAt: t }]);
    handleBrowserOpenRequests([{ requestId: 'shared', url: 'https://z', createdAt: t }]);
    expect(open).toHaveBeenCalledTimes(1);
  });
});
