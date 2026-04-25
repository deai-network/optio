import { Button, Dropdown, Tooltip, Popconfirm } from 'antd';
import type { MenuProps, ButtonProps } from 'antd';
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

const LAUNCHABLE_STATES = new Set(['idle', 'done', 'failed', 'cancelled']);

export interface LaunchControlsProps {
  process: any;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  size?: ButtonProps['size'];
}

/**
 * Renders launch affordances for a process:
 *   * Nothing when the process is in a non-launchable state.
 *   * Single play button when the task does not support resume (or has no
 *     saved state yet).
 *   * Split button (primary = Resume, menu = Restart) when supportsResume
 *     AND hasSavedState are both true.
 *
 * Defensive defaults: missing fields on the process document are treated
 * as false so the UI works against an unmigrated DB.
 */
export function LaunchControls({ process, onLaunch, size = 'small' }: LaunchControlsProps) {
  const { t } = useTranslation();
  const state = process?.status?.state ?? 'idle';
  if (!LAUNCHABLE_STATES.has(state) || !onLaunch) return null;

  const supportsResume = process.supportsResume === true;
  const hasSavedState = process.hasSavedState === true;

  // Case 1: single play button (fresh start semantics — no opts).
  if (!supportsResume || !hasSavedState) {
    const button = (
      <Button
        type="text"
        size={size}
        icon={<PlayCircleOutlined />}
        style={{ color: '#52c41a' }}
        onClick={(e) => {
          e.preventDefault();
          onLaunch(process._id, undefined);
        }}
      />
    );
    const wrapped = process.warning ? (
      <Popconfirm title={process.warning} onConfirm={() => onLaunch(process._id, undefined)}>
        {button}
      </Popconfirm>
    ) : button;
    return (
      <Tooltip title={t('processes.launch')}>{wrapped}</Tooltip>
    );
  }

  // Case 2: split button — primary = Resume, menu = Restart.
  const menu: MenuProps = {
    items: [
      {
        key: 'restart',
        icon: <ReloadOutlined />,
        label: t('processes.restart', { defaultValue: 'Restart (discard saved state)' }),
        onClick: () => onLaunch(process._id, { resume: false }),
      },
    ],
  };

  return (
    <Tooltip title={t('processes.resume', { defaultValue: 'Resume' })}>
      <Dropdown.Button
        size={size}
        trigger={['click']}
        icon={<PlayCircleOutlined />}
        menu={menu}
        onClick={() => onLaunch(process._id, { resume: true })}
      >
        <PlayCircleOutlined style={{ color: '#52c41a' }} />
      </Dropdown.Button>
    </Tooltip>
  );
}
