import { Tag, Tooltip } from 'antd';
import { ExclamationCircleOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useState, useEffect } from 'react';
import type { CSSProperties } from 'react';
import { isActiveState } from '../process-state.js';

const STATUS_COLORS: Record<string, string> = {
  idle: 'default',
  scheduled: 'cyan',
  running: 'blue',
  done: 'green',
  failed: 'red',
  cancel_requested: 'orange',
  cancelling: 'orange',
  cancelled: 'orange',
};

export type ProcessStatusBadgeSize = 'small' | 'default' | 'big';

// 'small' = antd Tag defaults (current look). Larger variants set fontSize +
// padding inline so the badge scales proportionally with the ProcessItem.
const SIZE_STYLE: Record<ProcessStatusBadgeSize, CSSProperties> = {
  small:   {},
  default: { fontSize: 14, padding: '2px 8px', lineHeight: '20px' },
  big:     { fontSize: 16, padding: '4px 12px', lineHeight: '24px' },
};

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function useElapsed(runningSince?: string | null, isActive?: boolean): string | null {
  const [elapsed, setElapsed] = useState<string | null>(null);

  useEffect(() => {
    if (!isActive || !runningSince) {
      setElapsed(null);
      return;
    }

    const start = new Date(runningSince).getTime();
    const update = () => setElapsed(formatElapsed((Date.now() - start) / 1000));
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [runningSince, isActive]);

  return elapsed;
}

interface ProcessStatusBadgeProps {
  state: string;
  error?: string;
  runningSince?: string | null;
  size?: ProcessStatusBadgeSize;
  /** When true, render a stopwatch indicator: this process is stamped for
   *  automatic resume after an engine restart. */
  autoResumeScheduled?: boolean;
}

export function ProcessStatusBadge({ state, error, runningSince, size = 'small', autoResumeScheduled }: ProcessStatusBadgeProps) {
  const { t } = useTranslation();
  const color = STATUS_COLORS[state] ?? 'default';
  const label = t(`status.${state}`, state);
  const isActive = isActiveState(state);
  const elapsed = useElapsed(runningSince, isActive);

  const autoResumeLabel = t('status.autoResumeScheduled', 'Scheduled for auto-restart');

  return (
    <span>
      <Tag color={color} style={SIZE_STYLE[size]}>
        {label}
        {elapsed && ` (${elapsed})`}
      </Tag>
      {state === 'failed' && error && (
        <Tooltip title={error}>
          <ExclamationCircleOutlined style={{ color: '#ff4d4f', marginLeft: 4 }} />
        </Tooltip>
      )}
      {autoResumeScheduled && (
        <Tooltip title={autoResumeLabel}>
          <ClockCircleOutlined aria-label={autoResumeLabel} style={{ color: '#722ed1', marginLeft: 4 }} />
        </Tooltip>
      )}
    </span>
  );
}
