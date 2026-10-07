import { List } from 'antd';
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
  return (
    <List
      loading={loading}
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
