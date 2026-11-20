import { Button, Tag, Typography, Empty } from 'antd';
import { memo, useEffect, useMemo, useRef, useState } from 'react';
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

/** How many of the most recent lines the panel renders; "Show earlier lines" adds as many more. */
export const LOG_PANEL_WINDOW = 500;

interface ProcessLogPanelProps {
  logs: LogEntry[];
  tree: ProcessTreeNode | null;
  /**
   * When true, the panel fills its parent's height (use with a flex-sized
   * container) instead of the default `maxHeight: 400`. Auto-scroll still
   * sticks to the bottom while the user hasn't manually scrolled up.
   */
  fillParent?: boolean;
  /**
   * How many older entries the log's buffer dropped before `logs[0]` (`useProcessStream`'s
   * `droppedLogCount`). Above 0, the top of the panel says older lines are not kept here, once
   * every line of `logs` shows. Default 0.
   */
  droppedLogCount?: number;
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

interface LogRowProps {
  entry: LogEntry;
  depth: number;
  color: string;
  label: string;
  /** The row names its process: the first row shown, or another process than the row above's. */
  transition: boolean;
  showBar: boolean;
}

/**
 * A row's key: its entry's identity, never its index. A full buffer drops its oldest entries as new
 * ones come (every index shifts), and "Show earlier lines" adds rows on top; each entry keeps its key
 * either way, so its memoized row does not render again.
 */
const entryKeys = new WeakMap<LogEntry, number>();
let nextEntryKey = 0;
function entryKey(entry: LogEntry): number {
  let key = entryKeys.get(entry);
  if (key === undefined) {
    key = nextEntryKey++;
    entryKeys.set(entry, key);
  }
  return key;
}

/** One line; memoized, so a new batch renders only the rows it adds. */
const LogRow = memo(function LogRow({ entry, depth, color, label, transition, showBar }: LogRowProps) {
  const indent = Math.min(depth, MAX_INDENT_DEPTH) * INDENT_PX;
  return (
    <div
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
            background: color,
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
          <Text style={{ color, fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap' }}>
            {label}
          </Text>
        )}
        <Text>{entry.message}</Text>
      </div>
    </div>
  );
});

export function ProcessLogPanel({ logs, tree, fillParent, droppedLogCount = 0 }: ProcessLogPanelProps) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);
  // Following the bottom, the panel renders the last `shown` lines. Scrolled up, it keeps its
  // first line (`anchor`), so the lines read stay put while new ones are added below.
  const [shown, setShown] = useState(LOG_PANEL_WINDOW);
  const [anchor, setAnchor] = useState<LogEntry | null>(null);

  const visuals = useMemo(() => buildProcessVisuals(tree), [tree]);

  const anchorIndex = anchor ? logs.lastIndexOf(anchor) : -1;
  const start = anchorIndex >= 0 ? anchorIndex : Math.max(0, logs.length - shown);

  const handleScroll = () => {
    if (!listRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = listRef.current;
    isAtBottomRef.current = scrollHeight - scrollTop - clientHeight < 30;
    if (isAtBottomRef.current) {
      if (anchor) {
        setAnchor(null);
        setShown(Math.max(LOG_PANEL_WINDOW, logs.length - start));
      }
    } else if (anchor !== logs[start]) {
      setAnchor(logs[start] ?? null);
    }
  };

  const showEarlier = () => {
    const earlier = Math.max(0, start - LOG_PANEL_WINDOW);
    setAnchor(logs[earlier] ?? null);
    setShown(logs.length - earlier);
  };

  // Every batch is a new array (a full buffer keeps its length), so follow on `logs`.
  useEffect(() => {
    if (listRef.current && isAtBottomRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [logs]);

  if (logs.length === 0) {
    return <Empty description={t('common.noData')} />;
  }

  const showBar = tree !== null;
  const rows = [];
  const usedKeys = new Set<number>();
  for (let i = start; i < logs.length; i++) {
    const entry = logs[i];
    const v = visualFor(visuals, entry);
    const id = entryKey(entry);
    // The same entry object twice in `logs` still gets a row each.
    const key = usedKeys.has(id) ? `${id}@${i}` : id;
    usedKeys.add(id);
    rows.push(
      <LogRow
        key={key}
        entry={entry}
        depth={v.depth}
        color={v.color}
        label={v.label}
        transition={i === start || logs[i - 1].processId !== entry.processId}
        showBar={showBar}
      />,
    );
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
      {start > 0 ? (
        <Button
          type="link"
          size="small"
          data-testid="log-show-earlier"
          style={{ padding: 0, height: 'auto', marginBottom: 4 }}
          onClick={showEarlier}
        >
          {t('processes.showEarlierLines', 'Show earlier lines')}
        </Button>
      ) : droppedLogCount > 0 ? (
        <div data-testid="log-older-not-kept" style={{ marginBottom: 4 }}>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {t('processes.olderLinesNotKept', 'Older lines are not kept here')}
          </Text>
        </div>
      ) : null}
      {rows}
    </div>
  );
}
