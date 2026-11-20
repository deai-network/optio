import { Button, Modal, Tooltip } from 'antd';
import type { ButtonProps } from 'antd';
import { DeleteOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { Process } from 'optio-contracts';
import { deny, useAction } from 'vultus-core';
import { ActionButton, CombinedActionButton } from 'vultus-antd';
import { optioErrorRoutes, type OptioErrorRouteId } from '../error-routes.js';
import { useProcessActions } from '../hooks/useProcessActions.js';
import { runEndedAt } from '../hooks/useProcessConversation.js';
import { useProcessLaunchActions, type LaunchOpts } from '../hooks/useProcessLaunchActions.js';
import { useProcessSnapshots } from '../hooks/useProcessSnapshots.js';
import { useTaskFactoryOf } from '../hooks/useTaskFactory.js';
import {
  isLaunchable, isResumable, isResurrecting, isTaskFactory, isTaskFactoryLaunchItself, isTerminal,
} from '../process-state.js';
import type { TaskFactoryOverrides } from '../task-factory-overrides.js';

export interface LaunchControlsProps {
  process: any;
  /** Launches the process: a fresh run (no opts, or `resume: false`), a resume
   *  (`resume: true`), or a resume from an older snapshot (`resume: true` and its
   *  `capturedAt` as `resumeFrom`). A task factory's play button opens its launch
   *  form instead, which launches it itself; `onLaunch` only allows it there. */
  onLaunch?: (processId: string, opts?: LaunchOpts) => void;
  /** A task factory's launch form launched it: the child it made. */
  onLaunched?: (child: Process) => void;
  /** Overrides of a task factory's launch form, by the factory's `processId`; its play button takes its entry. */
  launchOverrides?: Readonly<Record<string, TaskFactoryOverrides>>;
  /** Save the work a failed run left on its host, then resume. When given
   *  and the process is resurrectable, Resurrect becomes the primary action;
   *  the resumes and Restart move to the menu behind a confirmation. */
  onResurrect?: (processId: string) => void;
  size?: ButtonProps['size'];
  /** Optional pixel size for the inner icons of all the buttons rendered
   *  here. When unset, the icons inherit antd's default sizing for the
   *  chosen button size. */
  iconFontSize?: number;
  /** When set + non-empty, every launch action is rendered disabled with this
   *  string as its reason (the hover tooltip; the menu's items stay listed,
   *  disabled). Domain-specific launch gate: the caller decides launchability
   *  beyond the process state machine (e.g., from task metadata) and renders
   *  the operator-facing reason. Resurrecting keeps its own reason; a task
   *  factory's play button and a kept launch's Resume are replaced by a
   *  disabled play button. Cancel/etc. are unaffected. */
  denyReason?: string | null;
}

/**
 * Renders launch affordances for a process:
 *   * Nothing when the process is not launchable (`isLaunchable`: a
 *     non-launchable state, or a task factory's launch, never launched again).
 *   * For a task factory's ended launch, a Remove button; before it, when the
 *     launch is resumable, a Resume button (no play button and no Restart: a
 *     launch is never launched afresh), or the disabled play button of a
 *     non-empty `denyReason`.
 *   * For a task factory's record, a single play button that opens its
 *     launch form (TaskFactoryLaunchModal), which shows the warning itself.
 *   * Otherwise the actions of `useProcessLaunchActions` in vultus-antd's
 *     icon-only `CombinedActionButton`: Launch alone; Resume, the
 *     previous-snapshot items (from `useProcessSnapshots`) and Restart; in the
 *     resurrect case (`onResurrect` given) Resurrect, the resumes and Restart;
 *     while a resurrect runs, the disabled Resurrecting only. A menu pick
 *     leaves the main half on the first enabled action (`keepOriginalDefault`).
 *
 * A task's `warning` describes what a fresh run does: it is asked before
 * Launch and Restart (after Restart's own confirmation, if any), never before
 * a resume (Resume, a previous-snapshot item, Resurrect).
 *
 * Defensive defaults: missing fields on the process document are treated
 * as false so the UI works against an unmigrated DB.
 */
export function LaunchControls({
  process, onLaunch, onLaunched, launchOverrides, onResurrect, size = 'small', iconFontSize, denyReason,
}: LaunchControlsProps) {
  // Every hook runs before the early returns.
  const [modal, contextHolder] = Modal.useModal();
  const snapshots = useProcessSnapshots(process?._id, {
    enabled: !!onLaunch && isLaunchable(process) && isResumable(process) && !isTaskFactory(process),
    runEndedAt: runEndedAt(process),
  });
  const triggerLaunch = (opts?: LaunchOpts) => {
    if (!onLaunch) return;
    // A fresh run (Launch, Restart) asks the task's warning first; a resume does not.
    if (process.warning && !opts?.resume) {
      modal.confirm({ title: process.warning, onOk: () => onLaunch(process._id, opts) });
    } else {
      onLaunch(process._id, opts);
    }
  };
  // Without onResurrect, a resurrecting process still shows the disabled Resurrecting.
  const triggerResurrect = onResurrect
    ? () => onResurrect(process._id)
    : (isResurrecting(process) ? () => {} : undefined);
  const actions = useProcessLaunchActions(
    process, triggerLaunch, triggerResurrect, denyReason ? deny(denyReason) : true, snapshots,
  );

  if (!onLaunch) return null;
  const iconStyle = iconFontSize ? { fontSize: iconFontSize } : undefined;

  // A launch that ended and was kept is never launched again; it can be
  // removed, and resumed (the resume control a task has) when its task saved its state.
  // Only the launch itself: a record under it (its metadata inherited) has neither, as
  // optio-core dismisses and resumes the launch only.
  if (isTaskFactoryLaunchItself(process) && isTerminal(process)) {
    const remove = <RemoveLaunchButton process={process} size={size} iconStyle={iconStyle} />;
    if (!isResumable(process)) return remove;
    return (
      <>
        {denyReason
          ? <DeniedLaunchButton reason={denyReason} size={size} iconStyle={iconStyle} />
          : <ResumeButton process={process} onLaunch={onLaunch} size={size} iconStyle={iconStyle} />}
        {remove}
      </>
    );
  }
  if (!isLaunchable(process)) return null;

  // A task factory: its launch takes the values of its parameters first.
  if (isTaskFactory(process)) {
    return denyReason
      ? <DeniedLaunchButton reason={denyReason} size={size} iconStyle={iconStyle} />
      : (
        <FactoryPlayButton
          process={process} overrides={launchOverrides?.[process.processId]}
          onLaunched={onLaunched} size={size} iconStyle={iconStyle}
        />
      );
  }

  const styled = iconFontSize
    ? actions.map((a) => ({ ...a, icon: <span style={{ fontSize: iconFontSize }}>{a.icon}</span> }))
    : actions;
  // keepOriginalDefault: after a menu pick fires (or its confirmation is dismissed) the main half
  // shows the first enabled action again, so a click on it never repeats a pick such as Restart.
  return (
    <>
      {contextHolder}
      <CombinedActionButton actions={styled} size={size} iconOnly keepOriginalDefault />
    </>
  );
}

// The play button rendered disabled, its reason the hover tooltip. antd's Tooltip suppresses
// pointer events on disabled buttons: the span wrapper keeps the hover.
function DeniedLaunchButton({ reason, size, iconStyle }: {
  reason: string;
  size: ButtonProps['size'];
  iconStyle: { fontSize: number } | undefined;
}) {
  return (
    <Tooltip title={reason}>
      <span style={{ display: 'inline-block', cursor: 'not-allowed' }}>
        <Button
          type="text"
          size={size}
          icon={<PlayCircleOutlined style={iconStyle} />}
          disabled
          style={{ pointerEvents: 'none' }}
        />
      </span>
    </Tooltip>
  );
}

// Resumes a resumable kept launch from its saved state: its own control.
function ResumeButton({ process, onLaunch, size, iconStyle }: {
  process: any;
  onLaunch: (processId: string, opts?: LaunchOpts) => void;
  size: ButtonProps['size'];
  iconStyle: { fontSize: number } | undefined;
}) {
  const { t } = useTranslation();
  const label = t('processes.resume', { defaultValue: 'Resume' });
  return (
    <Tooltip title={label}>
      <Button
        type="text"
        size={size}
        aria-label={label}
        icon={<PlayCircleOutlined style={iconStyle} />}
        style={{ color: '#52c41a' }}
        onClick={(e) => {
          e.preventDefault();
          onLaunch(process._id, { resume: true });
        }}
      />
    </Tooltip>
  );
}

// The play button of a task factory's record (already in hand: no read of its own): the same
// action as TaskFactoryLaunchButton's, as the row's green icon button.
function FactoryPlayButton({ process, overrides, onLaunched, size, iconStyle }: {
  process: any;
  overrides: TaskFactoryOverrides | undefined;
  onLaunched?: (child: Process) => void;
  size: ButtonProps['size'];
  iconStyle: { fontSize: number } | undefined;
}) {
  const { launch, dialog } = useTaskFactoryOf(process, false, { overrides, onLaunched });
  return (
    <>
      <ActionButton
        action={{ ...launch, icon: <PlayCircleOutlined style={iconStyle} /> }}
        size={size}
        iconOnly
        buttonVariant="text"
        buttonColor="green"
      />
      {dialog}
    </>
  );
}

// Removes an ended launch that was kept (dismissing it deletes it, with its log, for good:
// it asks first).
function RemoveLaunchButton({ process, size, iconStyle }: {
  process: any;
  size: ButtonProps['size'];
  iconStyle: { fontSize: number } | undefined;
}) {
  const { t } = useTranslation();
  const { dismissAsync } = useProcessActions();
  const remove = useAction<void, void, OptioErrorRouteId>(
    {
      id: 'optio.taskFactory.removeLaunch',
      label: t('processes.removeLaunch', 'Remove'),
      icon: <DeleteOutlined style={iconStyle} />,
      confirmation: {
        kind: 'popconfirm',
        question: t('processes.removeLaunchConfirm', 'Remove this launch? Its record and log are deleted.'),
      },
      errorRoute: 'processes.dismiss',
      fire: () => dismissAsync(process._id),
    },
    optioErrorRoutes,
  );
  return <ActionButton action={remove} size={size} iconOnly buttonVariant="text" />;
}
