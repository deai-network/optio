import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useActionList } from '../useActionList.js';
import { denyWithReason } from '../decision.js';

describe('useActionList', () => {
  it('builds one ActionStatus per spec, preserving id/label/disabled/reason', () => {
    const { result } = renderHook(() =>
      useActionList([
        { id: 'a', label: 'A', fire: async () => {} },
        { id: 'b', label: 'B', enabled: () => denyWithReason('no'), fire: async () => {} },
      ]),
    );
    expect(result.current.map((x) => x.id)).toEqual(['a', 'b']);
    expect(result.current[0].label).toBe('A');
    expect(result.current[0].disabled).toBe(false);
    expect(result.current[1].disabled).toBe(true);
    expect(result.current[1].reason).toBe('no');
  });

  it('empty spec list yields no actions', () => {
    const { result } = renderHook(() => useActionList([]));
    expect(result.current).toEqual([]);
  });

  it('fires only the chosen action', async () => {
    const fireA = vi.fn(async () => {});
    const fireB = vi.fn(async () => {});
    const { result } = renderHook(() =>
      useActionList([
        { id: 'a', label: 'A', fire: fireA },
        { id: 'b', label: 'B', fire: fireB },
      ]),
    );
    await act(async () => {
      await result.current[1].firePromise();
    });
    expect(fireB).toHaveBeenCalledTimes(1);
    expect(fireA).not.toHaveBeenCalled();
  });

  it('isolates errors to the action that threw', async () => {
    const { result } = renderHook(() =>
      useActionList([
        { id: 'ok', label: 'ok', fire: async () => {} },
        {
          id: 'bad',
          label: 'bad',
          fire: async () => {
            throw { status: 500, body: { message: 'boom' } };
          },
        },
      ]),
    );
    await act(async () => {
      await result.current[1].firePromise();
    });
    expect(result.current[0].errors).toEqual([]);
    expect(result.current[1].errors).toEqual(['boom']);
  });

  it('tracks pending per id while one fire is in flight', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const { result } = renderHook(() =>
      useActionList([
        { id: 'slow', label: 'slow', fire: async () => { await gate; } },
        { id: 'idle', label: 'idle', fire: async () => {} },
      ]),
    );
    let inFlight: Promise<void>;
    act(() => {
      inFlight = result.current[0].firePromise();
    });
    expect(result.current[0].pending).toBe(true);
    expect(result.current[1].pending).toBe(false);
    await act(async () => {
      release();
      await inFlight;
    });
    expect(result.current[0].pending).toBe(false);
  });
});
