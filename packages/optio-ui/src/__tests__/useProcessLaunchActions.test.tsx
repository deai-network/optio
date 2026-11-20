import type { ReactNode } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { act, render, renderHook } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';
import type { ProcessSnapshot } from 'optio-contracts';
import { deny, type ActionStatus, type Decision } from 'vultus-core';
import { useProcessLaunchActions } from '../hooks/useProcessLaunchActions.js';
import { formatSnapshotTimes } from '../snapshot-time.js';

// No resources: every label is the hook's English default.
const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });
const wrapper = ({ children }: { children: ReactNode }) => (
  <I18nextProvider i18n={i18n}>{children}</I18nextProvider>
);

// A failed run whose host still holds unsaved work, with a saved snapshot.
const FAILED_WITH_UNSAVED_WORK = {
  _id: 'p1',
  status: { state: 'failed' },
  supportsResume: true,
  hasSavedState: true,
  supportsResurrect: true,
  hasUnsavedWork: true,
} as any;

const RESUMABLE = { _id: 'p1', status: { state: 'cancelled' }, supportsResume: true, hasSavedState: true };

const TS = '2026-11-19T13:05:12.345Z';
// Newest first, as useProcessSnapshots lists them.
const SNAPS: ProcessSnapshot[] = [
  { capturedAt: TS, endState: 'cancelled' },
  ...['12', '11', '10', '09', '08'].map((hour) => ({
    capturedAt: `2026-11-19T${hour}:05:00.000Z`, endState: 'cancelled',
  })),
];
const time = (i: number) => formatSnapshotTimes(SNAPS.slice(1, 5).map((s) => s.capturedAt), 'en')[i];
const previous = (i: number) => `Resume from previous snapshot (${time(i)})`;

function visible(
  process: any, triggerResurrect?: () => void, launchGate?: Decision, snapshots?: readonly ProcessSnapshot[],
) {
  const launch = vi.fn();
  const { result } = renderHook(() =>
    useProcessLaunchActions(process, launch, triggerResurrect, launchGate, snapshots), { wrapper });
  return { actions: result.current.filter((a) => !a.invisible), all: result.current, launch };
}

const slot = (all: ActionStatus[], s: number) => all.find((a) => a.id === `process.resumeFrom:p1:${s}`)!;

function contentText(action: ActionStatus): string {
  const confirmation = action.confirmation;
  if (confirmation?.kind !== 'cascade-modal') throw new Error('no cascade-modal confirmation');
  return render(<>{confirmation.content}</>).container.textContent ?? '';
}

describe('useProcessLaunchActions', () => {
  it('offers Resurrect first for a failed run with unsaved work', async () => {
    const resurrect = vi.fn();
    const { actions } = visible(FAILED_WITH_UNSAVED_WORK, resurrect);

    expect(actions.map((a) => a.label)).toEqual(
      ['Resurrect', 'Resume from last snapshot', 'Restart (discard saved state)']);
    expect(actions[0].disabled).toBe(false);
    await act(async () => { actions[0].fire(); });
    expect(resurrect).toHaveBeenCalledOnce();
  });

  it('asks before resuming or restarting over unsaved work', () => {
    const { actions } = visible(FAILED_WITH_UNSAVED_WORK, vi.fn());

    expect(actions[0].confirmation).toBeUndefined();
    for (const action of actions.slice(1)) {
      expect(action.confirmation?.kind).toBe('cascade-modal');
    }
  });

  it('offers Restart, not Resume, when the run has no saved snapshot', () => {
    const { actions } = visible({ ...FAILED_WITH_UNSAVED_WORK, hasSavedState: false }, vi.fn());

    expect(actions.map((a) => a.label)).toEqual(['Resurrect', 'Restart (discard saved state)']);
  });

  it('shows only a disabled Resurrecting while a resurrect runs', () => {
    const { actions } = visible({ ...FAILED_WITH_UNSAVED_WORK, resurrecting: true }, vi.fn());

    expect(actions).toHaveLength(1);
    expect(actions[0].label).toBe('Resurrecting…');
    expect(actions[0].disabled).toBe(true);
  });

  it('keeps Resume and Restart when there is no unsaved work', () => {
    const { actions } = visible({ ...FAILED_WITH_UNSAVED_WORK, hasUnsavedWork: false }, vi.fn());

    expect(actions.map((a) => a.label)).toEqual(['Resume', 'Restart (discard saved state)']);
    expect(actions.every((a) => a.confirmation === undefined)).toBe(true);
  });

  it('keeps the old buttons when the caller cannot resurrect', () => {
    const { actions } = visible(FAILED_WITH_UNSAVED_WORK);

    expect(actions.map((a) => a.label)).toEqual(['Resume', 'Restart (discard saved state)']);
  });

  it('holds every launch back with the gate\'s reason', () => {
    const cases = [
      FAILED_WITH_UNSAVED_WORK,
      { ...FAILED_WITH_UNSAVED_WORK, hasUnsavedWork: false },
      { ...FAILED_WITH_UNSAVED_WORK, hasUnsavedWork: false, hasSavedState: false },
    ];
    for (const process of cases) {
      const { actions } = visible(process, vi.fn(), deny('No free seed'));
      expect(actions.length).toBeGreaterThan(0);
      for (const action of actions) {
        expect(action.disabled).toBe(true);
        expect(action.reason).toBe('No free seed');
      }
    }
  });

  it('keeps Resurrecting\'s own reason under a gate', () => {
    const { actions } = visible({ ...FAILED_WITH_UNSAVED_WORK, resurrecting: true }, vi.fn(), deny('No free seed'));

    expect(actions.map((a) => a.reason)).toEqual(
      ['Saving the work the failed run left behind; the process resumes afterwards.']);
  });

  it('offers one item per older snapshot, newest first, at most four', () => {
    const { actions, all } = visible(RESUMABLE, undefined, undefined, SNAPS);

    expect(actions.map((a) => a.label)).toEqual(
      ['Resume', ...[0, 1, 2, 3].map(previous), 'Restart (discard saved state)']);
    expect(all.length).toBe(8);
  });

  it('no items without older snapshots', () => {
    for (const snapshots of [undefined, [], [SNAPS[0]]]) {
      const { actions } = visible(RESUMABLE, undefined, undefined, snapshots);
      expect(actions.map((a) => a.label)).toEqual(['Resume', 'Restart (discard saved state)']);
    }
  });

  it('an item resumes from its snapshot after a confirmation naming the newer ones', async () => {
    const { all, launch } = visible(RESUMABLE, undefined, undefined, SNAPS);
    const item = slot(all, 1);

    expect(item.confirmation?.kind).toBe('cascade-modal');
    expect(item.confirmation?.kind === 'cascade-modal' && item.confirmation.title)
      .toBe(`Resume from ${time(1)}? This discards 2 newer snapshots.`);
    const text = contentText(item);
    expect(text).toContain('What the discarded runs delivered stays in the app.');
    expect(text).not.toContain('unsaved work');

    await act(async () => { item.fire(); });
    expect(launch).toHaveBeenCalledWith({ resume: true, resumeFrom: SNAPS[2].capturedAt });

    const first = slot(all, 0);
    expect(first.confirmation?.kind === 'cascade-modal' && first.confirmation.title)
      .toMatch(/This discards 1 newer snapshot\.$/);
  });

  it('resurrect case: items between Resume from last snapshot and Restart, confirmation names the unsaved work', () => {
    const { actions, all } = visible(FAILED_WITH_UNSAVED_WORK, vi.fn(), undefined, SNAPS);

    expect(actions.map((a) => a.label)).toEqual([
      'Resurrect', 'Resume from last snapshot', ...[0, 1, 2, 3].map(previous), 'Restart (discard saved state)',
    ]);
    for (const s of [0, 1, 2, 3]) {
      const text = contentText(slot(all, s));
      expect(text).toContain('What the discarded runs delivered stays in the app.');
      expect(text).toContain('This discards the unsaved work the failed run left behind.');
    }
    const item = slot(all, 2);
    expect(item.confirmation?.kind === 'cascade-modal' && item.confirmation.title)
      .toBe(`Resume from ${time(2)}? This discards 3 newer snapshots.`);
  });

  it('resurrecting: Resurrecting only', () => {
    const { actions } = visible({ ...FAILED_WITH_UNSAVED_WORK, resurrecting: true }, vi.fn(), undefined, SNAPS);

    expect(actions.map((a) => a.label)).toEqual(['Resurrecting…']);
  });

  it('resurrecting: the icon is the loading one when no Spinner is lent', () => {
    const { actions } = visible({ ...FAILED_WITH_UNSAVED_WORK, resurrecting: true }, vi.fn());
    const { container } = render(<>{actions[0].icon}</>);

    expect(container.querySelector('.anticon-loading')).not.toBeNull();
    expect(container.querySelector('.anticon-medicine-box')).toBeNull();
  });

  it('a language with more plural forms still gets the confirmation text', () => {
    // Polish: 2 is a "few" form, which the hook has no default of its own for.
    const pl = i18next.createInstance();
    pl.init({ lng: 'pl', resources: { pl: { translation: {} } } });
    const plWrapper = ({ children }: { children: ReactNode }) => (
      <I18nextProvider i18n={pl}>{children}</I18nextProvider>
    );
    const { result } = renderHook(
      () => useProcessLaunchActions(RESUMABLE, vi.fn(), undefined, true, SNAPS), { wrapper: plWrapper });
    const plTime = formatSnapshotTimes(SNAPS.slice(1, 5).map((s) => s.capturedAt), 'pl')[1];
    const item = slot(result.current, 1);

    expect(item.confirmation?.kind === 'cascade-modal' && item.confirmation.title)
      .toBe(`Resume from ${plTime}? This discards 2 newer snapshots.`);
  });

  it('the gate holds the items back with its reason', () => {
    const cases: Array<[any, (() => void) | undefined]> = [
      [RESUMABLE, undefined],
      [FAILED_WITH_UNSAVED_WORK, vi.fn()],
    ];
    for (const [process, resurrect] of cases) {
      const { actions } = visible(process, resurrect, deny('No free seed'), SNAPS);
      expect(actions.filter((a) => a.id.startsWith('process.resumeFrom:'))).toHaveLength(4);
      for (const action of actions) {
        expect(action.disabled).toBe(true);
        expect(action.reason).toBe('No free seed');
      }
    }
  });

  it('no items for a process that is not launchable or not resumable', () => {
    expect(visible({ ...RESUMABLE, status: { state: 'running' } }, undefined, undefined, SNAPS).actions)
      .toEqual([]);
    expect(visible({ ...RESUMABLE, hasSavedState: false }, undefined, undefined, SNAPS).actions
      .map((a) => a.label)).toEqual(['Launch']);
  });

  it('a fixed number of statuses and hooks', () => {
    const launch = vi.fn();
    type Props = { process: any; snapshots: readonly ProcessSnapshot[] | undefined };
    const initialProps: Props = { process: null, snapshots: undefined };
    const { result, rerender } = renderHook(
      (p: Props) => useProcessLaunchActions(p.process, launch, undefined, true, p.snapshots),
      { wrapper, initialProps },
    );
    expect(result.current.length).toBe(8);

    rerender({ process: RESUMABLE, snapshots: SNAPS });
    expect(result.current.length).toBe(8);
    expect(result.current.filter((a) => a.id.startsWith('process.resumeFrom:')).map((a) => a.id)).toEqual(
      ['process.resumeFrom:p1:0', 'process.resumeFrom:p1:1', 'process.resumeFrom:p1:2', 'process.resumeFrom:p1:3']);

    rerender({ process: RESUMABLE, snapshots: [] });
    expect(result.current.length).toBe(8);
  });

  it('items whose minutes collide show seconds', () => {
    const snapshots: ProcessSnapshot[] = [
      { capturedAt: TS, endState: 'cancelled' },
      { capturedAt: '2026-11-19T12:05:40.000Z', endState: 'cancelled' },
      { capturedAt: '2026-11-19T12:05:10.000Z', endState: 'cancelled' },
    ];
    const { all } = visible(RESUMABLE, undefined, undefined, snapshots);

    expect(slot(all, 0).invisible).toBe(false);
    expect(slot(all, 1).invisible).toBe(false);
    expect(slot(all, 0).label).not.toBe(slot(all, 1).label);
  });
});
