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
  /** Extra class on the status tag, e.g. a host app's tag look. */
  className?: string;
  /** The task can resume: stopping it suspends it (its state is saved), so
   *  the stop reads "Suspending". */
  supportsResume?: boolean;
  /** A saved state exists: a cancelled, resumable process is "Suspended". */
  hasSavedState?: boolean;
}

// Stopping a process whose task can resume saves its state: it is suspended,
// not cancelled, and shown so, in a calm color (owner, 2026-10-14).
const STOPPING_STATES = new Set(['cancel_requested', 'cancelling']);
const SUSPEND_COLOR = 'blue';

export function ProcessStatusBadge({
  state, error, runningSince, size = 'small', autoResumeScheduled, className, supportsResume, hasSavedState,
}: ProcessStatusBadgeProps) {
  const { t } = useTranslation();
  const suspending = Boolean(supportsResume) && STOPPING_STATES.has(state);
  const suspended = Boolean(supportsResume && hasSavedState) && state === 'cancelled';
  const color = suspending || suspended ? SUSPEND_COLOR : STATUS_COLORS[state] ?? 'default';
  const label = suspending ? t('status.suspending', 'Suspending')
    : suspended ? t('status.suspended', 'Suspended')
      : t(`status.${state}`, state);
  const isActive = isActiveState(state);
  const elapsed = useElapsed(runningSince, isActive);

  const autoResumeLabel = t('status.autoResumeScheduled', 'Scheduled for auto-restart');

  return (
    <span>
      <Tag color={color} style={SIZE_STYLE[size]} className={className}>
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
