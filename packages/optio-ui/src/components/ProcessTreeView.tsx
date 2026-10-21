import { useMemo, useState } from 'react';
import { Tree, Progress, Button, Tooltip, Typography, Checkbox } from 'antd';
import { CloseCircleOutlined, PauseCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { Process } from 'optio-contracts';
import { ProcessStatusBadge } from './ProcessStatusBadge.js';
import { LaunchControls } from './LaunchControls.js';
import { isActive as isProcessActive, isResurrecting } from '../process-state.js';
import type { DataNode } from 'antd/es/tree';

const { Text } = Typography;

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
  onLaunch?: (processId: string, opts?: { resume?: boolean }) => void;
  onLaunched?: (child: Process) => void;
  onResurrect?: (processId: string) => void;
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
  const { onCancel, onLaunch, onLaunched, onResurrect } = actions;
  const isActive = isProcessActive(node);
  // A resurrect runs while the state stays failed; show its progress message.
  const showsProgressMessage = isActive || isResurrecting(node);
  const isCancellable = isActive && node.cancellable;
  const suspends = Boolean(node.supportsResume);
  const stopLabel = suspends ? t('processes.suspend', 'Suspend') : t('processes.cancel');
  const hasPercent = node.progress.percent != null;

  return {
    key: node._id,
    title: (
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0 8px', width: '100%' }}>
        {node.description ? (
          <Tooltip title={node.description}>
            <Text style={{ whiteSpace: 'nowrap' }}>{node.name}</Text>
          </Tooltip>
        ) : (
          <Text style={{ whiteSpace: 'nowrap' }}>{node.name}</Text>
        )}
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
              process={node as any} onLaunch={onLaunch} onLaunched={onLaunched} onResurrect={onResurrect} size="small"
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
  treeData, sseState, onCancel, onLaunch, onLaunched, onResurrect,
}: ProcessTreeViewProps) {
  const { t } = useTranslation();
  const [hideFinishedLeaves, setHideFinishedLeaves] = useState(true);

  const filtered = treeData ? (hideFinishedLeaves ? filterDoneChildren(treeData) : treeData) : null;
  const expandedKeys = useMemo(() => filtered ? collectKeys(filtered) : [], [filtered]);

  if (!treeData || !filtered) return null;

  const treeNodes = [treeNodeToDataNode(filtered, { onCancel, onLaunch, onLaunched, onResurrect }, t)];

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
      />
    </div>
  );
}
