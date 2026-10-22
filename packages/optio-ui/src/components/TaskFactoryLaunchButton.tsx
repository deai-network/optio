import type { ButtonProps } from 'antd';
import type { Process } from 'optio-contracts';
import { ActionButton } from 'vultus-antd';
import { useTaskFactory } from '../hooks/useTaskFactory.js';
import type { TaskFactoryOverrides } from '../task-factory-overrides.js';

export interface TaskFactoryLaunchButtonProps {
  /** The factory's process id as the app declared it, or its record's `_id`. */
  factoryId: string;
  /** Per field id, a partial vultus-core definition merged into that field of the launch form. */
  overrides?: TaskFactoryOverrides;
  /** Called with the child once the launch is accepted. */
  onLaunched?: (child: Process) => void;
  size?: ButtonProps['size'];
}

/** A task factory's launch button: opens its launch form (`useTaskFactory`). */
export function TaskFactoryLaunchButton({ factoryId, overrides, onLaunched, size }: TaskFactoryLaunchButtonProps) {
  const { launch, dialog } = useTaskFactory(factoryId, { overrides, onLaunched });
  return (
    <>
      <ActionButton action={launch} size={size} />
      {dialog}
    </>
  );
}
