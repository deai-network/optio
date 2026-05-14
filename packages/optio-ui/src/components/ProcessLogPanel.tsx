import { Tag, Typography, Empty } from 'antd';
import { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import type { LogEntry, ProcessTreeNode } from '../hooks/useProcessStream.js';
import { buildProcessVisuals, type ProcessVisual } from '../log-visuals.js';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  event: 'cyan',
  info: 'blue',
  debug: 'default',
  warning: 'gold',
  error: 'red',
};

const INDENT_PX = 16;
const MAX_INDENT_DEPTH = 8;
const UNKNOWN_COLOR = '#666';

interface ProcessLogPanelProps {
  logs: LogEntry[];
  tree: ProcessTreeNode | null;
  /**
   * When true, the panel fills its parent's height (use with a flex-sized
   * container) instead of the default `maxHeight: 400`. Auto-scroll still
   * sticks to the bottom while the user hasn't manually scrolled up.
   */
  fillParent?: boolean;
}

function visualFor(
  visuals: Map<string, ProcessVisual>,
  entry: LogEntry,
): ProcessVisual {
  return (
    visuals.get(entry.processId) ?? {
      depth: 0,
      color: UNKNOWN_COLOR,
      label: entry.processLabel,
    }
  );
}

export function ProcessLogPanel({ logs, tree, fillParent }: ProcessLogPanelProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);

  const visuals = useMemo(() => buildProcessVisuals(tree), [tree]);

  const handleScroll = () => {
    if (listRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = listRef.current;
      isAtBottomRef.current = scrollHeight - scrollTop - clientHeight < 30;
    }
  };

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
      {logs.map((entry, idx) => {
        const v = visualFor(visuals, entry);
        const prev = idx > 0 ? logs[idx - 1] : null;
        const transition = !prev || prev.processId !== entry.processId;
        const indent = Math.min(v.depth, MAX_INDENT_DEPTH) * INDENT_PX;
        const showBar = tree !== null;

        return (
          <div
            key={idx}
            data-testid="log-row"
            style={{
              display: 'flex',
              alignItems: 'baseline',
              marginBottom: 2,
              paddingLeft: indent,
            }}
          >
            {showBar && (
              <div
                data-testid="log-bar"
                style={{
                  width: 3,
                  alignSelf: 'stretch',
                  background: v.color,
                  marginRight: 8,
                  flex: '0 0 auto',
                }}
              />
            )}
            <div
              style={{
                display: 'flex',
                gap: 8,
                alignItems: 'baseline',
                flex: 1,
                minWidth: 0,
              }}
            >
              <Text type="secondary" style={{ whiteSpace: 'nowrap', fontSize: 11 }}>
                {new Date(entry.timestamp).toLocaleTimeString()}
              </Text>
              <Tag color={LEVEL_COLORS[entry.level] ?? 'default'} style={{ fontSize: 10 }}>
                {entry.level.toUpperCase()}
              </Tag>
              {transition && (
                <Text style={{ color: v.color, fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap' }}>
                  {v.label}
                </Text>
              )}
              <Text>{entry.message}</Text>
            </div>
          </div>
        );
      })}
    </div>
  );
}
