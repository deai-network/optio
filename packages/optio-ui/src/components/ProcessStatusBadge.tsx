import { Tag, Tooltip } from 'antd';
import { ExclamationCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useState, useEffect } from 'react';

const STATUS_COLORS: Record<string, string> = {
  idle: 'default',
  scheduled: 'cyan',
  running: 'blue',
  done: 'green',
  failed: 'red',
  cancel_requested: 'orange',
  cancelling: 'orange',
  cancelled: 'default',
};

const ACTIVE_STATES = new Set(['running', 'scheduled', 'cancel_requested', 'cancelling']);

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
}

export function ProcessStatusBadge({ state, error, runningSince }: ProcessStatusBadgeProps) {
  const { t } = useTranslation();
  const color = STATUS_COLORS[state] ?? 'default';
  const label = t(`status.${state}`, state);
  const isActive = ACTIVE_STATES.has(state);
  const elapsed = useElapsed(runningSince, isActive);

  return (
    <span>
      <Tag color={color}>
        {label}
        {elapsed && ` (${elapsed})`}
      </Tag>
      {state === 'failed' && error && (
        <Tooltip title={error}>
          <ExclamationCircleOutlined style={{ color: '#ff4d4f', marginLeft: 4 }} />
        </Tooltip>
      )}
    </span>
  );
}
