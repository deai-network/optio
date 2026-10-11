import type { CSSProperties, ReactNode } from 'react';
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

/**
 * The List's loading indicator around a lent Spinner. antd's Spin clones its `indicator` and
 * injects `className` (incl. `ant-spin-dot`, a 1em box), `style` and `percent`; they land here,
 * so the Spinner receives only its own props. `percent` is dropped, and the inline styles undo
 * `.ant-spin-dot`'s box so the Spinner keeps the size it asks for.
 */
function SpinnerSlotIndicator({ className, style, children }: {
  className?: string;
  style?: CSSProperties;
  percent?: number;
  children: ReactNode;
}) {
  return (
    <span
      className={['optio-spinner-slot-indicator', className].filter(Boolean).join(' ')}
      style={{ ...style, display: 'inline-flex', width: 'auto', height: 'auto', fontSize: 'inherit' }}
    >
      {children}
    </span>
  );
}

export function ProcessList({ processes, loading, onLaunch, onResurrect, onCancel, onProcessClick }: ProcessListProps) {
  const { Spinner } = useVultusComponents();
  const spinnerKey = useSpinnerKey('optio:process-list');
  return (
    <List
      loading={Spinner
        ? {
          spinning: loading,
          indicator: <SpinnerSlotIndicator><Spinner spinnerKey={spinnerKey} size="default" /></SpinnerSlotIndicator>,
        }
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
