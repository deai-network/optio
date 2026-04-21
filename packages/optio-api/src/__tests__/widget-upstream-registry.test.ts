import { describe, it, expect, beforeEach, vi } from 'vitest';
import { createWidgetUpstreamRegistry } from '../widget-upstream-registry.js';

describe('widgetUpstreamRegistry', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('caches a value and returns it within TTL', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    reg.set('proc1', { url: 'http://a', innerAuth: null });
    expect(reg.get('proc1')).toEqual({ url: 'http://a', innerAuth: null });
  });

  it('returns undefined for an unknown key', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    expect(reg.get('nope')).toBeUndefined();
  });

  it('expires after TTL', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    reg.set('proc1', { url: 'http://a', innerAuth: null });
    vi.advanceTimersByTime(5001);
    expect(reg.get('proc1')).toBeUndefined();
  });

  it('supports explicit invalidate', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    reg.set('proc1', { url: 'http://a', innerAuth: null });
    reg.invalidate('proc1');
    expect(reg.get('proc1')).toBeUndefined();
  });

  it('stores null as a distinct cached-miss value', () => {
    const reg = createWidgetUpstreamRegistry({ ttlMs: 5000 });
    reg.set('proc1', null);
    expect(reg.has('proc1')).toBe(true);
    expect(reg.get('proc1')).toBeNull();
  });
});
