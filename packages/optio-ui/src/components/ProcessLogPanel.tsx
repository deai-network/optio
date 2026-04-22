import { Tag, Typography, Empty } from 'antd';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  event: 'cyan',
  info: 'blue',
  debug: 'default',
  warning: 'gold',
  error: 'red',
};

interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  processName?: string;
}

interface ProcessLogPanelProps {
  logs: LogEntry[];
  /**
   * When true, the panel fills its parent's height (use with a flex-sized
   * container) instead of the default `maxHeight: 400`. Auto-scroll still
   * sticks to the bottom while the user hasn't manually scrolled up.
   */
  fillParent?: boolean;
}

export function ProcessLogPanel({ logs, fillParent }: ProcessLogPanelProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);

  const handleScroll = () => {
    if (listRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = listRef.current;
      isAtBottomRef.current = scrollHeight - scrollTop - clientHeight < 30;
    }
  };

  // Auto-scroll to bottom only when user is already at bottom
  useEffect(() => {
    if (listRef.current && isAtBottomRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [logs.length]);

  if (logs.length === 0) {
    return <Empty description={t('common.noData')} />;
  }

  return (
    <div
      ref={listRef}
      onScroll={handleScroll}
      style={{
        ...(fillParent ? { height: '100%' } : { maxHeight: 400 }),
        overflow: 'auto',
        border: '1px solid #303030',
        borderRadius: 4,
        padding: 8,
        fontFamily: 'monospace',
        fontSize: 12,
      }}
    >
      {logs.map((entry, idx) => (
        <div key={idx} style={{ display: 'flex', gap: 8, marginBottom: 4, alignItems: 'baseline' }}>
          <Text type="secondary" style={{ whiteSpace: 'nowrap', fontSize: 11 }}>
            {new Date(entry.timestamp).toLocaleTimeString()}
          </Text>
          <Tag color={LEVEL_COLORS[entry.level] ?? 'default'} style={{ fontSize: 10 }}>
            {entry.level.toUpperCase()}
          </Tag>
          {entry.processName && (
            <Text type="secondary" style={{ fontSize: 11 }}>[{entry.processName}]</Text>
          )}
          <Text>{entry.message}</Text>
        </div>
      ))}
    </div>
  );
}
