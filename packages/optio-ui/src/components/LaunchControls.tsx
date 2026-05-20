import { Button, Dropdown, Space, Tooltip, Popconfirm } from 'antd';
import type { MenuProps, ButtonProps } from 'antd';
import { DownOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { isLaunchable, isResumable } from '../process-state.js';

export interface LaunchControlsProps {
  process: any;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  size?: ButtonProps['size'];
  /** Optional pixel size for the inner icons (Play/Down/Reload). When unset,
   *  the icon inherits antd's default sizing for the chosen button size. */
  iconFontSize?: number;
  /** When set + non-empty, the launch button is rendered disabled with this
   *  string as the hover tooltip. Domain-specific launch gate: the caller
   *  decides launchability beyond the process state machine (e.g., from
   *  task metadata) and renders the operator-facing reason. Suppresses both
   *  the single-button and split-button (resume) branches; cancel/etc. are
   *  unaffected. */
  denyReason?: string | null;
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
export function LaunchControls({ process, onLaunch, size = 'small', iconFontSize, denyReason }: LaunchControlsProps) {
  const { t } = useTranslation();
  if (!isLaunchable(process) || !onLaunch) return null;
  const iconStyle = iconFontSize ? { fontSize: iconFontSize } : undefined;

  // Caller-injected gate: launchable per state machine but domain-denied.
  // Render a single disabled play button with the reason as tooltip.
  // antd's Tooltip suppresses pointer events on disabled buttons; wrap in
  // a span so hover still surfaces the reason.
  if (denyReason) {
    return (
      <Tooltip title={denyReason}>
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

  // Case 1: single play button (fresh start semantics — no opts).
  if (!isResumable(process)) {
    const button = (
      <Button
        type="text"
        size={size}
        icon={<PlayCircleOutlined style={iconStyle} />}
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
    <Space.Compact>
      <Tooltip title={t('processes.resume', { defaultValue: 'Resume' })}>
        <Button
          type="text"
          size={size}
          icon={<PlayCircleOutlined style={iconStyle} />}
          style={{ color: '#52c41a' }}
          onClick={(e) => {
            e.preventDefault();
            onLaunch(process._id, { resume: true });
          }}
        />
      </Tooltip>
      <Dropdown menu={menu} trigger={['click']}>
        <Tooltip title={t('processes.moreOptions', { defaultValue: 'More options' })}>
          <Button
            type="text"
            size={size}
            icon={<DownOutlined style={iconStyle} />}
          />
        </Tooltip>
      </Dropdown>
    </Space.Compact>
  );
}
