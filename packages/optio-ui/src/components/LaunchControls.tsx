import { Button, Dropdown, Modal, Space, Tooltip, Popconfirm } from 'antd';
import type { MenuProps, ButtonProps } from 'antd';
import { DownOutlined, MedicineBoxOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { isLaunchable, isResumable, isResurrectable } from '../process-state.js';

export interface LaunchControlsProps {
  process: any;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  /** Save the work a failed run left on its host, then resume. When given
   *  and the process is resurrectable, Resurrect becomes the primary action;
   *  Resume-from-snapshot and Restart move to the menu behind a confirmation. */
  onResurrect?: (processId: string) => void;
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
 *   * Split button (primary = Resurrect, menu = Resume from last snapshot /
 *     Restart, each behind a "discards the unsaved work" confirmation) when
 *     `onResurrect` is given and the process is resurrectable
 *     (supportsResurrect AND hasUnsavedWork). The resume item is shown only
 *     when the process is also resumable.
 *   * Single play button when the task does not support resume (or has no
 *     saved state yet).
 *   * Split button (primary = Resume, menu = Restart) when supportsResume
 *     AND hasSavedState are both true.
 *
 * Defensive defaults: missing fields on the process document are treated
 * as false so the UI works against an unmigrated DB.
 */
export function LaunchControls({
  process, onLaunch, onResurrect, size = 'small', iconFontSize, denyReason,
}: LaunchControlsProps) {
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

  // Case 0: resurrect — the host still holds a failed run's unsaved work.
  if (onResurrect && isResurrectable(process)) {
    return (
      <ResurrectControls
        process={process} onLaunch={onLaunch} onResurrect={onResurrect}
        size={size} iconStyle={iconStyle}
      />
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

function ResurrectControls({ process, onLaunch, onResurrect, size, iconStyle }: {
  process: any;
  onLaunch: (processId: string, opts?: { resume?: boolean }) => void;
  onResurrect: (processId: string) => void;
  size: ButtonProps['size'];
  iconStyle: { fontSize: number } | undefined;
}) {
  const { t } = useTranslation();
  const [modal, contextHolder] = Modal.useModal();
  const confirmDiscard = (title: string, action: () => void) => {
    modal.confirm({
      title,
      content: t('processes.discardUnsavedWork', {
        defaultValue: 'This discards the unsaved work left by the failed run.',
      }),
      okText: t('processes.discardAndContinue', { defaultValue: 'Discard and continue' }),
      okButtonProps: { danger: true },
      onOk: action,
    });
  };
  const resumeLabel = t('processes.resumeFromSnapshot', { defaultValue: 'Resume from last snapshot' });
  const restartLabel = t('processes.restartDiscarding', { defaultValue: 'Restart' });
  const items: MenuProps['items'] = [];
  if (isResumable(process)) {
    items.push({
      key: 'resume',
      icon: <PlayCircleOutlined />,
      label: resumeLabel,
      onClick: () => confirmDiscard(resumeLabel, () => onLaunch(process._id, { resume: true })),
    });
  }
  items.push({
    key: 'restart',
    icon: <ReloadOutlined />,
    label: restartLabel,
    onClick: () => confirmDiscard(restartLabel, () => onLaunch(process._id, { resume: false })),
  });
  return (
    <>
      {contextHolder}
      <Space.Compact>
        <Tooltip title={t('processes.resurrectHint', {
          defaultValue: 'Save the work left by the failed run, then resume',
        })}>
          <Button
            type="text"
            size={size}
            aria-label={t('processes.resurrect', { defaultValue: 'Resurrect' })}
            icon={<MedicineBoxOutlined style={iconStyle} />}
            style={{ color: '#fa8c16' }}
            onClick={(e) => {
              e.preventDefault();
              onResurrect(process._id);
            }}
          />
        </Tooltip>
        <Dropdown menu={{ items }} trigger={['click']}>
          <Tooltip title={t('processes.moreOptions', { defaultValue: 'More options' })}>
            <Button type="text" size={size} icon={<DownOutlined style={iconStyle} />} />
          </Tooltip>
        </Dropdown>
      </Space.Compact>
    </>
  );
}
