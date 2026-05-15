import { List } from 'antd';
import { ProcessItem } from './ProcessItem.js';

interface ProcessListProps {
  processes: any[];
  loading: boolean;
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onCancel?: (processId: string) => void;
  onProcessClick?: (processId: string) => void;
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
