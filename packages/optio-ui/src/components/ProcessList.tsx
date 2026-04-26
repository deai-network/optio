import { List, Button, Progress, Tooltip, Typography } from 'antd';
import { CloseCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { HighlightedText } from '@quaesitor-textus/core';
import { ProcessStatusBadge } from './ProcessStatusBadge.js';
import { LaunchControls } from './LaunchControls.js';

const { Text } = Typography;

const ACTIVE_STATES = new Set(['running', 'scheduled', 'cancel_requested', 'cancelling']);

interface ProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}

export function ProcessItem({ process, onLaunch, onCancel, readonly, onProcessClick }: { process: any; onLaunch?: (id: string, opts?: { resume?: boolean }) => void; onCancel?: (id: string) => void; readonly?: boolean; onProcessClick?: (id: string) => void }) {
  const { t } = useTranslation();
  const state = process.status?.state ?? 'idle';
  const isActive = ACTIVE_STATES.has(state);
  const isCancellable = !readonly && isActive && process.cancellable;
  const hasPercent = process.progress?.percent != null;

  const nameContent = onProcessClick ? (
    <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => onProcessClick(process._id)}>
      <HighlightedText text={process.name} all />
    </Button>
  ) : (
    <Text><HighlightedText text={process.name} all /></Text>
  );

  const nameElement = process.description ? (
    <Tooltip title={process.description}>{nameContent}</Tooltip>
  ) : nameContent;

  return (
    <div style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>
          {nameElement}
          {isActive && process.progress?.message && (
            <Text style={{ marginLeft: 8, color: '#1890ff' }}>— {process.progress.message}</Text>
          )}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ProcessStatusBadge state={state} error={process.status?.error} runningSince={process.status?.runningSince} />
          {!readonly && <LaunchControls process={process} onLaunch={onLaunch} size="small" />}
          {isCancellable && onCancel && (
            <Tooltip title={t('processes.cancel')}>
              <Button type="text" size="small" danger icon={<CloseCircleOutlined />}
                onClick={(e) => { e.preventDefault(); onCancel(process._id); }} />
            </Tooltip>
          )}
        </div>
      </div>
      {isActive && hasPercent ? (
        <Progress percent={process.progress.percent} size="small" showInfo={false}
          status={state === 'failed' ? 'exception' : 'active'} />
      ) : isActive ? (
        <Progress percent={100} status="active" size="small" showInfo={false} strokeColor={{ from: '#108ee9', to: '#87d068' }} />
      ) : null}
    </div>
  );
}

export function ProcessList({ processes, loading, onLaunch, onCancel, onProcessClick }: ProcessListProps) {
  return (
    <List
      loading={loading}
      dataSource={processes}
      pagination={{ pageSize: 16 }}
      renderItem={(item: any) => (
        <List.Item>
          <ProcessItem process={item} onLaunch={onLaunch} onCancel={onCancel} onProcessClick={onProcessClick} />
        </List.Item>
      )}
    />
  );
}
