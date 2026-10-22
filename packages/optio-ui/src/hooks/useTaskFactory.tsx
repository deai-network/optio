import { useState, type ReactNode } from 'react';
import { PlayCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { Process } from 'optio-contracts';
import { useAction, type ActionStatus } from 'vultus-core';
import { TaskFactoryLaunchModal } from '../components/TaskFactoryLaunchModal.js';
import { isTaskFactory } from '../process-state.js';
import type { TaskFactoryOverrides } from '../task-factory-overrides.js';
import { useProcess } from './useProcessQueries.js';
import type { TaskFactoryRecord } from './useTaskFactoryLaunch.js';

export interface UseTaskFactoryOptions {
  /** Per field id, a partial vultus-core definition merged into that field of the launch form. */
  overrides?: TaskFactoryOverrides;
  /** Called with the child once the launch is accepted. */
  onLaunched?: (child: Process) => void;
  /** The `launch` action's label, instead of "Launch" (`processes.launch`): the app's own word
   *  for what the factory does, e.g. "Drop" or "Generate proposals" in a menu. */
  label?: string;
}

export interface UseTaskFactoryResult {
  /** Opens the launch form; disabled, with its reason, while the record loads, when the process
   *  is no task factory, and while a launch is pending. */
  launch: ActionStatus;
  /** A launch is in flight: from the form's submit to the engine's answer. (`launch.pending` is
   *  not that: `launch` only opens the form.) */
  isPending: boolean;
  /** The launch form's modal, for the app to render once. */
  dialog: ReactNode;
}

/**
 * Launching a task factory from anywhere in an app (spec 2026-10-22-task-factory-launch-options
 * §3.3): `factoryId` is the factory's process id as the app declared it, or its record's `_id`;
 * the hook reads the record. Render `dialog` once and give `launch` to an `ActionButton`.
 */
export function useTaskFactory(factoryId: string, options: UseTaskFactoryOptions = {}): UseTaskFactoryResult {
  const { process, isLoading } = useProcess(factoryId);
  return useTaskFactoryOf(process, isLoading, options);
}

/**
 * As `useTaskFactory`, for a caller that already holds the record (a list's row, a tree's node):
 * no read of its own. `null` is a record not (yet) found.
 */
export function useTaskFactoryOf(
  record: (TaskFactoryRecord & { taskFactory?: boolean }) | null | undefined,
  isLoading: boolean,
  { overrides, onLaunched, label }: UseTaskFactoryOptions = {},
): UseTaskFactoryResult {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [isPending, setPending] = useState(false);
  const factory = record && isTaskFactory(record) ? record : null;
  const launch = useAction({
    id: 'optio.taskFactory.open',
    label: label ?? t('processes.launch', 'Launch'),
    icon: <PlayCircleOutlined />,
    enabled: () => {
      if (isLoading) return { verdict: false, reason: t('processes.factoryLoading', 'Loading…') };
      if (!factory) return { verdict: false, reason: t('processes.factoryNotAFactory', 'Not a task factory') };
      if (isPending) return { verdict: false, reason: t('processes.factoryLaunching', 'Launching…') };
      return true;
    },
    fire: () => setOpen(true),
  });
  const dialog = factory && (
    <TaskFactoryLaunchModal
      process={factory}
      open={open}
      onClose={() => setOpen(false)}
      onLaunched={onLaunched}
      overrides={overrides}
      onPendingChange={setPending}
    />
  );
  return { launch, isPending, dialog };
}
