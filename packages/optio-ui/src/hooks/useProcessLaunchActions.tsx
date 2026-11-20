import { useTranslation } from 'react-i18next';
import {
  HistoryOutlined, LoadingOutlined, MedicineBoxOutlined, PlayCircleOutlined, ReloadOutlined,
} from '@ant-design/icons';
import type { ProcessSnapshot } from 'optio-contracts';
import {
  useAction, useSpinnerKey, useVultusComponents,
  type ActionOptions, type ActionStatus, type ConfirmationSpec, type Decision,
} from 'vultus-core';
import {
  isLaunchable, isResumable, isResurrectable, isResurrecting, type ProcessStateLike,
} from '../process-state.js';
import { formatSnapshotTimes } from '../snapshot-time.js';

/** What a launch action asks `triggerLaunch` for: a fresh run (undefined, or `resume: false`), a
 *  resume from the newest snapshot (`resume: true`), or one from an older snapshot (`resume: true`
 *  and its `capturedAt` as `resumeFrom`). */
export type LaunchOpts = { resume?: boolean; resumeFrom?: string } | undefined;

// The predicates take a ProcessStateLike (status and flags); the action ids and
// the launch need the process id too.
type LaunchableProcess = ProcessStateLike & { _id: string };

// A snapshot's time goes into the text as it is: React escapes what it renders.
const RAW = { escapeValue: false };

/**
 * The launch controls for one process as declarative action descriptors, for vultus-antd's
 * CombinedActionButton. Always eight statuses, in this order: Resurrect, Resume, the four
 * previous-snapshot slots, Restart, Launch. Visibility encodes the launchable/resumable matrix,
 * so the button renders the right shape with no caller-side branching:
 *
 *   - resurrecting                → Resurrecting… (disabled) only
 *   - resurrectable (a failed run left unsaved work on its host, the task can
 *     save it, and the caller passes `triggerResurrect`)
 *                                 → Resurrect (primary) + Resume from last
 *                                   snapshot and the previous-snapshot items (if
 *                                   resumable) + Restart; all but Resurrect
 *                                   discard the unsaved work, so they ask first
 *   - launchable && resumable     → Resume (primary) + the previous-snapshot
 *                                   items + Restart
 *   - launchable && !resumable    → Launch (primary) only (renders as a single
 *                                   button)
 *   - !launchable (incl. no proc) → all invisible (CombinedActionButton renders
 *                                   nothing)
 *
 * The previous-snapshot items: `snapshots` is `useProcessSnapshots`' list (newest first, the
 * newest included, which Resume reads). Slot `s` (0 to 3) offers `snapshots[s + 1]`, "Resume from
 * previous snapshot (<time>)", behind a confirmation that names its `s + 1` newer snapshots, which
 * the resume discards (and, in the resurrect case, the unsaved work); it fires
 * `triggerLaunch({ resume: true, resumeFrom: capturedAt })`. With no list (loading, failed, not
 * passed) or no older snapshot, the slots are invisible: the menu is Resume and Restart.
 *
 * Every action that launches fires the caller's `triggerLaunch`: Resume, Restart, Launch, the
 * resurrect case's Resume from last snapshot and Restart, and the previous-snapshot items. The
 * hook does not ask a task's `warning`: `triggerLaunch` must ask the process's warning first, for
 * each of them (after the action's own confirmation, if it has one), and launch exactly once after
 * OK; a cancel launches nothing. Resurrect fires `triggerResurrect`, and it starts the task's run
 * again too: `triggerResurrect` must ask the process's warning first as well, and resurrect exactly
 * once after OK; a cancel resurrects nothing.
 *
 * `launchGate` (default: allowed) holds every action that launches -- Resurrect resumes after
 * saving -- back with its reason, e.g. while no seed is free; Resurrecting keeps its own. The
 * `useAction`s are called unconditionally, always the same number, so it is safe to invoke before
 * a page's early returns (rules of hooks); a null process just yields all-invisible.
 */
export function useProcessLaunchActions(
  process: LaunchableProcess | null | undefined,
  triggerLaunch: (opts?: LaunchOpts) => void,
  triggerResurrect?: () => void,
  launchGate: Decision = true,
  snapshots?: readonly ProcessSnapshot[],
): ActionStatus[] {
  const { t, i18n } = useTranslation();
  const { Spinner } = useVultusComponents();
  const id = process?._id ?? '';
  const resurrectingKey = useSpinnerKey(`optio:process:${id}:resurrecting`);
  const launchable = !!process && isLaunchable(process);
  const resumable = !!process && isResumable(process);
  const resurrecting = !!process && isResurrecting(process);
  const resurrectable = !!triggerResurrect && !!process && isResurrectable(process);

  const discardUnsavedWork = t('processes.discardUnsavedWork', {
    defaultValue: 'This discards the unsaved work the failed run left behind.',
  });
  const discardsUnsavedWork = (label: string): ConfirmationSpec | undefined =>
    resurrectable ? { kind: 'cascade-modal', title: label, content: discardUnsavedWork } : undefined;

  const resurrect = useAction({
    id: `process.resurrect:${id}`,
    label: resurrecting
      ? t('processes.resurrecting', { defaultValue: 'Resurrecting…' })
      : t('processes.resurrect', { defaultValue: 'Resurrect' }),
    icon: resurrecting
      ? (Spinner ? <Spinner spinnerKey={resurrectingKey} size="small" /> : <LoadingOutlined spin />)
      : <MedicineBoxOutlined />,
    variant: 'primary',
    enabled: resurrecting
      ? {
        verdict: false,
        reason: t('processes.resurrectingReason', {
          defaultValue: 'Saving the work the failed run left behind; the process resumes afterwards.',
        }),
      }
      : launchGate,
    invisible: !(resurrectable || (resurrecting && !!triggerResurrect)),
    fire: () => triggerResurrect?.(),
  });

  const resumeLabel = resurrectable
    ? t('processes.resumeFromSnapshot', { defaultValue: 'Resume from last snapshot' })
    : t('processes.resume', { defaultValue: 'Resume' });
  const resume = useAction({
    id: `process.resume:${id}`,
    label: resumeLabel,
    icon: <PlayCircleOutlined />,
    variant: resurrectable ? 'default' : 'primary',
    enabled: launchGate,
    invisible: resurrecting || !(launchable && resumable),
    confirmation: discardsUnsavedWork(resumeLabel),
    fire: () => triggerLaunch({ resume: true }),
  });

  // The snapshots after the newest, at most four: one slot (one useAction) each.
  const older = snapshots?.slice(1, 5) ?? [];
  const times = formatSnapshotTimes(older.map((s) => s.capturedAt), i18n.resolvedLanguage ?? i18n.language);
  const keepsDeliveries = t('processes.resumeFromPreviousKeepsDeliveries', {
    defaultValue: 'What the discarded runs delivered stays in the app.',
  });
  // Slot s: the snapshot s + 1 places below the newest, with s + 1 newer ones to discard.
  const previousSnapshot = (s: number): ActionOptions => {
    const snapshot = older[s];
    const time = times[s] ?? '';
    return {
      id: `process.resumeFrom:${id}:${s}`,
      label: t('processes.resumeFromPrevious', {
        time, defaultValue: 'Resume from previous snapshot ({{time}})', interpolation: RAW,
      }),
      icon: <HistoryOutlined />,
      variant: 'default',
      enabled: launchGate,
      invisible: !snapshot || resurrecting || !(launchable && resumable),
      confirmation: snapshot
        ? {
          kind: 'cascade-modal',
          title: t('processes.resumeFromPreviousConfirm', {
            time,
            count: s + 1,
            defaultValue_one: 'Resume from {{time}}? This discards {{count}} newer snapshot.',
            defaultValue_other: 'Resume from {{time}}? This discards {{count}} newer snapshots.',
            // A language's other plural forms (few, many, ...) without a translation.
            defaultValue: 'Resume from {{time}}? This discards {{count}} newer snapshots.',
            interpolation: RAW,
          }),
          content: resurrectable ? `${keepsDeliveries} ${discardUnsavedWork}` : keepsDeliveries,
        }
        : undefined,
      fire: () => {
        if (snapshot) triggerLaunch({ resume: true, resumeFrom: snapshot.capturedAt });
      },
    };
  };
  const previous0 = useAction(previousSnapshot(0));
  const previous1 = useAction(previousSnapshot(1));
  const previous2 = useAction(previousSnapshot(2));
  const previous3 = useAction(previousSnapshot(3));

  const restartLabel = t('processes.restart', { defaultValue: 'Restart (discard saved state)' });
  const restart = useAction({
    id: `process.restart:${id}`,
    label: restartLabel,
    icon: <ReloadOutlined />,
    enabled: launchGate,
    invisible: resurrecting || !(launchable && (resumable || resurrectable)),
    confirmation: discardsUnsavedWork(restartLabel),
    fire: () => triggerLaunch({ resume: false }),
  });

  const launch = useAction({
    id: `process.launch:${id}`,
    label: t('processes.launch', { defaultValue: 'Launch' }),
    icon: <PlayCircleOutlined />,
    variant: 'primary',
    enabled: launchGate,
    invisible: resurrecting || resurrectable || !(launchable && !resumable),
    fire: () => triggerLaunch(),
  });

  return [resurrect, resume, previous0, previous1, previous2, previous3, restart, launch];
}
