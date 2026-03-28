import { useMemo, useState } from 'react';
import { Tree, Progress, Button, Tooltip, Typography, Checkbox } from 'antd';
import { CloseCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { ProcessStatusBadge } from './ProcessStatusBadge.js';
import type { DataNode } from 'antd/es/tree';

const { Text } = Typography;

const ACTIVE_STATES = new Set(['running', 'scheduled', 'cancel_requested', 'cancelling']);

interface ProcessNode {
  _id: string;
  name: string;
  description?: string | null;
  status: { state: string; error?: string; runningSince?: string };
  progress: { percent: number | null; message?: string };
  cancellable?: boolean;
  children?: ProcessNode[];
}

interface SseState {
  connected: boolean;
}

interface ProcessTreeViewProps {
  treeData: ProcessNode | null;
  sseState: SseState;
  onCancel?: (processId: string) => void;
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

function treeNodeToDataNode(
  node: ProcessNode,
  onCancel: ((id: string) => void) | undefined,
  t: (key: string) => string,
): DataNode {
  const isActive = ACTIVE_STATES.has(node.status.state);
  const isCancellable = isActive && node.cancellable;
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
        <ProcessStatusBadge state={node.status.state} error={node.status.error} runningSince={node.status.runningSince} />
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 120 }}>
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
              style={{ flex: 1 }}
              status={node.status.state === 'failed' ? 'exception' : 'active'}
            />
          ) : isActive ? (
            <Progress
              percent={100}
              status="active"
              size="small"
              showInfo={false}
              style={{ flex: 1 }}
              strokeColor={{ from: '#108ee9', to: '#87d068' }}
            />
          ) : null}
          {isActive && hasPercent && (
            <Text style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{Math.round(node.progress.percent!)}%</Text>
          )}
          {isCancellable && onCancel && (
            <Tooltip title={t('processes.cancel')}>
              <Button
                type="text"
                size="small"
                danger
                icon={<CloseCircleOutlined />}
                onClick={(e) => {
                  e.stopPropagation();
                  onCancel(node._id);
                }}
              />
            </Tooltip>
          )}
        </span>
        {isActive && node.progress.message && (
          <Text style={{ width: '100%', fontSize: 12, color: '#1890ff' }}>— {node.progress.message}</Text>
        )}
      </div>
    ),
    children: node.children?.map((child) => treeNodeToDataNode(child, onCancel, t)) ?? [],
  };
}

export function ProcessTreeView({ treeData, sseState, onCancel }: ProcessTreeViewProps) {
  const { t } = useTranslation();
  const [hideFinishedLeaves, setHideFinishedLeaves] = useState(true);

  if (!treeData) return null;

  const filtered = hideFinishedLeaves ? filterDoneChildren(treeData) : treeData;
  const treeNodes = [treeNodeToDataNode(filtered, onCancel, t)];
  const expandedKeys = useMemo(() => collectKeys(filtered), [filtered]);

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
