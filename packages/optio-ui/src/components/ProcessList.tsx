import { List } from 'antd';
import { useSpinnerKey, useVultusComponents } from 'vultus-core';
import { ProcessItem } from './ProcessItem.js';

interface ProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onResurrect?: (processId: string) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
}

export function ProcessList({ processes, loading, onLaunch, onResurrect, onCancel, onProcessClick }: ProcessListProps) {
  const { Spinner } = useVultusComponents();
  const spinnerKey = useSpinnerKey('optio:process-list');
  return (
    <List
      loading={Spinner
        ? { spinning: loading, indicator: <Spinner spinnerKey={spinnerKey} size="default" /> }
        : loading}
      dataSource={processes}
      pagination={{ pageSize: 16 }}
      renderItem={(item: any) => (
        <List.Item>
          <ProcessItem
            process={item} onLaunch={onLaunch} onResurrect={onResurrect} onCancel={onCancel}
            onProcessClick={onProcessClick} size="small"
          />
        </List.Item>
      )}
    />
  );
}
