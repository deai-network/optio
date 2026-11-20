import { useLayoutEffect, useMemo, useRef, useState, type Key } from 'react';
import { Tree, Progress, Button, Tooltip, Typography, Checkbox } from 'antd';
import { CloseCircleOutlined, PauseCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { Process } from 'optio-contracts';
import { ProcessStatusBadge } from './ProcessStatusBadge.js';
import { LaunchControls } from './LaunchControls.js';
import { isActive as isProcessActive, isResurrecting } from '../process-state.js';
import type { LaunchOpts } from '../hooks/useProcessLaunchActions.js';
import type { DataNode } from 'antd/es/tree';

const { Text } = Typography;

/** Above this many nodes shown (after "Hide finished sub-tasks"), the tree renders virtualized. */
export const LARGE_TREE_THRESHOLD = 500;
/** The most a large tree's box takes (px): antd Tree's virtual mode renders the rows in view. */
export const LARGE_TREE_HEIGHT = 600;

interface ProcessNode {
  _id: string;
  name: string;
  description?: string | null;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable?: boolean;
  warning?: string;
  supportsResume?: boolean;
  hasSavedState?: boolean;
  supportsResurrect?: boolean;
  hasUnsavedWork?: boolean;
  resurrecting?: boolean;
  autoResumeScheduled?: boolean;
  taskFactory?: boolean;        // a task factory's record: its launch opens its launch form
  parameters?: unknown[];
  children?: ProcessNode[];
}

interface SseState {
  connected: boolean;
}

/** What a node's controls call. */
interface NodeActions {
  onCancel?: (processId: string) => void;
  onLaunch?: (processId: string, opts?: LaunchOpts) => void;
  onLaunched?: (child: Process) => void;
  onResurrect?: (processId: string) => void;
  /** A node's name is a link button calling this with the node's `_id`; without it, plain text. */
  onProcessClick?: (id: string) => void;
}

interface ProcessTreeViewProps extends NodeActions {
  treeData: ProcessNode | null;
  sseState: SseState;
}

function filterDoneChildren(node: ProcessNode): ProcessNode {
  const children = node.children
    ?.filter((child) => child.status.state !== 'done')
    .map((child) => filterDoneChildren(child));
  return { ...node, children };
}

function countNodes(node: ProcessNode): number {
  let count = 1;
  for (const child of node.children ?? []) count += countNodes(child);
  return count;
}

function collectKeys(node: ProcessNode): string[] {
  const keys = [node._id];
  if (node.children) {
    for (const child of node.children) {
      keys.push(...collectKeys(child));
    }
  }
  return keys;
}

function treeNodeToDataNode(node: ProcessNode, actions: NodeActions, t: TFunction): DataNode {
  const { onCancel, onLaunch, onLaunched, onResurrect, onProcessClick } = actions;
  const isActive = isProcessActive(node);
  // A resurrect runs while the state stays failed; show its progress message.
  const showsProgressMessage = isActive || isResurrecting(node);
  const isCancellable = isActive && node.cancellable;
  const suspends = Boolean(node.supportsResume);
  const stopLabel = suspends ? t('processes.suspend', 'Suspend') : t('processes.cancel');
  const hasPercent = node.progress.percent != null;

  // As ProcessItem's name: a link button when the caller handles clicks, plain text otherwise.
  const nameContent = onProcessClick ? (
    <Button
      type="link"
      style={{ padding: 0, height: 'auto', whiteSpace: 'nowrap' }}
      onClick={() => onProcessClick(node._id)}
    >
      {node.name}
    </Button>
  ) : (
    <Text style={{ whiteSpace: 'nowrap' }}>{node.name}</Text>
  );

  return {
    key: node._id,
    title: (
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0 8px', width: '100%' }}>
        {node.description ? (
          <Tooltip title={node.description}>{nameContent}</Tooltip>
        ) : nameContent}
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto', minWidth: 120, justifyContent: 'flex-end' }}>
          <ProcessStatusBadge
            state={node.status.state} error={node.status.error} runningSince={node.status.runningSince}
            autoResumeScheduled={node.autoResumeScheduled}
            supportsResume={node.supportsResume} hasSavedState={node.hasSavedState}
          />
          {/* Progress bar visibility rules (keep consistent across ProcessList,
              RecentProcesses):
              - active with percent: determinate bar
              - active without percent: indeterminate animated bar
              - not active: hidden */}
          {isActive && hasPercent ? (
            <Progress
              percent={node.progress.percent!}
              size="small"
              showInfo={false}
              style={{ width: 120 }}
              status={node.status.state === 'failed' ? 'exception' : 'active'}
            />
          ) : isActive ? (
            <Progress
              percent={100}
              status="active"
              size="small"
              showInfo={false}
              style={{ width: 120 }}
              strokeColor={{ from: '#108ee9', to: '#87d068' }}
            />
          ) : null}
          {isActive && hasPercent && (
            <Text style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{Math.round(node.progress.percent!)}%</Text>
          )}
          {isCancellable && onCancel && (
            // A process whose task can resume is suspended (its state is
            // saved), not cancelled: no danger color.
            <Tooltip title={stopLabel}>
              <Button
                type="text"
                size="small"
                danger={!suspends}
                aria-label={stopLabel}
                icon={suspends ? <PauseCircleOutlined /> : <CloseCircleOutlined />}
                onClick={(e) => {
                  e.stopPropagation();
                  onCancel(node._id);
                }}
              />
            </Tooltip>
          )}
          {onLaunch && (
            <LaunchControls
              process={node as any} onLaunch={onLaunch} onLaunched={onLaunched}
              onResurrect={onResurrect} size="small"
            />
          )}
        </span>
        {showsProgressMessage && node.progress.message && (
          <Text style={{ width: '100%', fontSize: 12, color: '#1890ff' }}>— {node.progress.message}</Text>
        )}
      </div>
    ),
    children: node.children?.map((child) => treeNodeToDataNode(child, actions, t)) ?? [],
  };
}

export function ProcessTreeView({
  treeData, sseState, onCancel, onLaunch, onLaunched, onResurrect, onProcessClick,
}: ProcessTreeViewProps) {
  const { t } = useTranslation();
  const [hideFinishedLeaves, setHideFinishedLeaves] = useState(true);
  // A large tree's expanded nodes as the user left them, for that tree's root only.
  const [opened, setOpened] = useState<{ root: string; keys: Key[] } | null>(null);

  // The controls call the latest callbacks given. So the nodes are rebuilt when which
  // callbacks are given changes, not for a caller's new inline functions on each render.
  const latest = useRef<NodeActions>({});
  useLayoutEffect(() => {
    latest.current = { onCancel, onLaunch, onLaunched, onResurrect, onProcessClick };
  });
  const hasCancel = Boolean(onCancel);
  const hasLaunch = Boolean(onLaunch);
  const hasLaunched = Boolean(onLaunched);
  const hasResurrect = Boolean(onResurrect);
  const hasProcessClick = Boolean(onProcessClick);
  const actions = useMemo<NodeActions>(() => ({
    onCancel: hasCancel ? (id) => latest.current.onCancel?.(id) : undefined,
    onLaunch: hasLaunch ? (id, opts) => latest.current.onLaunch?.(id, opts) : undefined,
    onLaunched: hasLaunched ? (child) => latest.current.onLaunched?.(child) : undefined,
    onResurrect: hasResurrect ? (id) => latest.current.onResurrect?.(id) : undefined,
    onProcessClick: hasProcessClick ? (id) => latest.current.onProcessClick?.(id) : undefined,
  }), [hasCancel, hasLaunch, hasLaunched, hasResurrect, hasProcessClick]);

  const filtered = useMemo(
    () => (treeData ? (hideFinishedLeaves ? filterDoneChildren(treeData) : treeData) : null),
    [treeData, hideFinishedLeaves],
  );
  const large = useMemo(() => (filtered ? countNodes(filtered) : 0), [filtered]) > LARGE_TREE_THRESHOLD;
  // Every node expanded, as before; a large tree opens with its root's children only.
  const expandedKeys = useMemo<Key[]>(() => {
    if (!filtered) return [];
    if (!large) return collectKeys(filtered);
    return opened?.root === filtered._id ? opened.keys : [filtered._id];
  }, [filtered, large, opened]);
  const treeNodes = useMemo(
    () => (filtered ? [treeNodeToDataNode(filtered, actions, t)] : []),
    [filtered, actions, t],
  );

  if (!treeData || !filtered) return null;

  const virtual = large
    ? {
        height: LARGE_TREE_HEIGHT,
        onExpand: (keys: Key[]) => setOpened({ root: filtered._id, keys }),
      }
    : {};

  return (
    <div>
      <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {sseState.connected ? 'Live' : 'Disconnected'}
        </Text>
        <Checkbox checked={hideFinishedLeaves} onChange={(e) => setHideFinishedLeaves(e.target.checked)}>
          Hide finished sub-tasks
        </Checkbox>
      </div>
      <style>{`
        .process-tree .ant-tree-treenode { display: flex; width: 100%; }
        .process-tree .ant-tree-node-content-wrapper { flex: 1; overflow: hidden; }
        .process-tree .ant-tree-title { display: block; }
      `}</style>
      <Tree
        className="process-tree"
        treeData={treeNodes}
        expandedKeys={expandedKeys}
        selectable={false}
        showLine
        {...virtual}
      />
    </div>
  );
}
