import type { ReactNode } from 'react';
import { Button, Progress, Tooltip, Typography } from 'antd';
import type { ButtonProps, ProgressProps } from 'antd';
import { CloseCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { HighlightedText } from '@quaesitor-textus/core';
import { ProcessStatusBadge } from './ProcessStatusBadge.js';
import { LaunchControls } from './LaunchControls.js';
import { isActive as isProcessActive, isCancellable as isProcessCancellable } from '../process-state.js';

const { Text } = Typography;

export type ProcessItemSize = 'small' | 'default' | 'big';

interface SizeTokens {
  button: ButtonProps['size'];
  progress: ProgressProps['size'];
  /** Pixel font-size for action-button icons. Undefined = antd default. */
  iconFontSize?: number;
  /** Pixel font-size for the process name on the left. Undefined = antd default. */
  nameFontSize?: number;
}

const SIZE_TOKENS: Record<ProcessItemSize, SizeTokens> = {
  small:   { button: 'small',  progress: 'small' },
  default: { button: 'middle', progress: 'default', nameFontSize: 16 },
  big:     { button: 'large',  progress: { height: 12 }, iconFontSize: 24, nameFontSize: 20 },
};

export interface ProcessItemProps {
  process: any;
  onLaunch?: (id: string, opts?: { resume?: boolean }) => void;
  onCancel?: (id: string) => void;
  readonly?: boolean;
  onProcessClick?: (id: string) => void;
  size?: ProcessItemSize;
  /** When true, lay out name / progress bar / actions on a single row
   *  (progress takes the flex-grow middle slot). When false (default),
   *  progress goes on its own row below the name+actions row. */
  inline?: boolean;
  /** Optional content rendered where the progress bar would be when the
   *  process is NOT active (inline mode: middle slot; non-inline mode:
   *  the row below the name/actions). Lets callers surface last-run
   *  context — "Started X (Y ago), finished Z (elapsed W)" etc. —
   *  without bloating ProcessItem with excavator-specific state. */
  inactiveContent?: ReactNode;
}

export function ProcessItem({
  process, onLaunch, onCancel, readonly, onProcessClick,
  size = 'default', inline = false, inactiveContent,
}: ProcessItemProps) {
  const { t } = useTranslation();
  const state = process.status?.state ?? 'idle';
  const isActive = isProcessActive(process);
  const isCancellable = !readonly && isProcessCancellable(process);
  const hasPercent = process.progress?.percent != null;
  const tokens = SIZE_TOKENS[size];

  const nameStyle = tokens.nameFontSize ? { fontSize: tokens.nameFontSize } : undefined;
  const nameContent = onProcessClick ? (
    <Button
      type="link"
      style={{ padding: 0, height: 'auto', ...nameStyle }}
      onClick={() => onProcessClick(process._id)}
    >
      <HighlightedText text={process.name} all />
    </Button>
  ) : (
    <Text style={nameStyle}><HighlightedText text={process.name} all /></Text>
  );

  const nameElement = process.description ? (
    <Tooltip title={process.description}>{nameContent}</Tooltip>
  ) : nameContent;

  const nameBlock = (
    <span>
      {nameElement}
      {isActive && process.progress?.message && (
        <Text style={{ marginLeft: 8, color: '#1890ff' }}>— {process.progress.message}</Text>
      )}
    </span>
  );

  const progressBar = isActive && hasPercent ? (
    <Progress percent={process.progress.percent} size={tokens.progress} showInfo={false}
      status={state === 'failed' ? 'exception' : 'active'} />
  ) : isActive ? (
    <Progress percent={100} status="active" size={tokens.progress} showInfo={false}
      strokeColor={{ from: '#108ee9', to: '#87d068' }} />
  ) : null;

  // Active → progress bar; inactive → caller's slot (may be undefined).
  const middleSlot: ReactNode = isActive ? progressBar : (inactiveContent ?? null);

  const actionBlock = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <ProcessStatusBadge
        state={state}
        error={process.status?.error}
        runningSince={process.status?.runningSince}
        size={size}
      />
      {!readonly && (
        <LaunchControls
          process={process}
          onLaunch={onLaunch}
          size={tokens.button}
          iconFontSize={tokens.iconFontSize}
        />
      )}
      {isCancellable && onCancel && (
        <Tooltip title={t('processes.cancel')}>
          <Button
            type="text"
            size={tokens.button}
            danger
            icon={<CloseCircleOutlined style={tokens.iconFontSize ? { fontSize: tokens.iconFontSize } : undefined} />}
            onClick={(e) => { e.preventDefault(); onCancel(process._id); }}
          />
        </Tooltip>
      )}
    </div>
  );

  if (inline) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, width: '100%' }}>
        {nameBlock}
        <div style={{ flex: 1, minWidth: 80 }}>{middleSlot}</div>
        {actionBlock}
      </div>
    );
  }

  return (
    <div style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {nameBlock}
        {actionBlock}
      </div>
      {middleSlot}
    </div>
  );
}
