import { describe, it, expect, beforeEach, vi } from 'vitest';
import { handleBrowserOpenRequests, __resetBrowserOpenSeenForTest } from '../handlers/browserOpen.js';

describe('handleBrowserOpenRequests', () => {
  beforeEach(() => {
    __resetBrowserOpenSeenForTest();
    vi.restoreAllMocks();
  });

  it('opens each url exactly once across repeated deliveries (dedup by requestId)', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    const reqs = [{ requestId: 'r1', url: 'https://x' }];
    handleBrowserOpenRequests(reqs);
    handleBrowserOpenRequests(reqs); // re-delivered on the next poll tick
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith('https://x', '_blank', 'noopener,noreferrer');
  });

  it('strips surrounding quotes from a captured url', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    handleBrowserOpenRequests([{ requestId: 'r2', url: '"https://q"' }]);
    expect(open).toHaveBeenCalledWith('https://q', '_blank', 'noopener,noreferrer');
  });

  it('is a no-op for empty/undefined input', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    handleBrowserOpenRequests(undefined);
    handleBrowserOpenRequests([]);
    expect(open).not.toHaveBeenCalled();
  });

  it('dedups across distinct feed chokepoints (shared module-level Set)', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    // Same requestId arriving from the list feed then the tree feed.
    handleBrowserOpenRequests([{ requestId: 'shared', url: 'https://z' }]);
    handleBrowserOpenRequests([{ requestId: 'shared', url: 'https://z' }]);
    expect(open).toHaveBeenCalledTimes(1);
  });
});
