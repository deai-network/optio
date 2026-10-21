import type { ReactNode } from 'react';
import { Spin } from 'antd';
import type { Process } from 'optio-contracts';
import { useSpinnerKey, useVultusComponents } from 'vultus-core';
import { useProcessStream } from '../hooks/useProcessStream.js';
import { isActive } from '../process-state.js';
import { ProcessTreeView } from './ProcessTreeView.js';
import { ProcessLogPanel } from './ProcessLogPanel.js';
import { useProcessWidgetState } from './ProcessWidget.js';
import { useProcessActions } from '../hooks/useProcessActions.js';
import { LaunchControls } from './LaunchControls.js';

export interface ProcessDetailViewProps {
  processId: string | null | undefined;
  /**
   * When true, suppress all per-process action affordances (Launch and
   * Cancel controls rendered by `ProcessTreeView`). The view continues
   * to stream and render tree / logs / widget identically. Defaults to
   * false.
   *
   * Intended for embeds where the host page already owns the action
   * affordances, or where re-running the process from inside the embed
   * doesn't make semantic sense — e.g. a debug-run pane that displays
   * the result of a one-shot ephemeral process; relaunching it from
   * here would silently re-execute with stale form input.
   */
  readOnly?: boolean;
  /**
   * When false, hide the log panel(s) and render only the widget at full
   * height. For end-user-facing embeds that want just the conversation
   * widget without the admin log strip — the loading / not-found / error
   * readiness states are still rendered. Defaults to true (logs shown).
   */
  showLogs?: boolean;
  /**
   * When true, the phase before the process's widget is available is
   * hidden: until the widget shows, a big spinner (the app's lent Spinner,
   * else antd's) takes the place of the "Loading…" line and of the process
   * view (tree and logs) of a scheduled or running process. For embeds that
   * show a process as its widget only, e.g. a chat. A process that ends
   * without its widget shows the process view, as without it; not-found and
   * stream errors show as without it. Defaults to false.
   */
  waitForWidget?: boolean;
  /**
   * Shown instead of the big spinner while waiting for the widget. Passing
   * it implies `waitForWidget`.
   */
  widgetPlaceholder?: ReactNode;
  /**
   * A task factory's launch form (its play button in the tree opens it)
   * launched it: the child it made, which shows under the factory.
   */
  onLaunched?: (child: Process) => void;
}

export function ProcessDetailView({
  processId,
  readOnly = false,
  showLogs = true,
  waitForWidget = false,
  widgetPlaceholder,
  onLaunched,
}: ProcessDetailViewProps) {
  const { tree, logs, connected, processNotFound, error } =
    useProcessStream(processId ?? undefined);
  const { launch, cancel, resurrect } = useProcessActions();
  // readOnly reaches the widget too: a suspended conversation's composer
  // would otherwise resume the process.
  const widgetResult = useProcessWidgetState(tree, { readOnly });
  const { Spinner } = useVultusComponents();
  const spinnerKey = useSpinnerKey(`optio:process:${processId}:detail`);
  const waiting = waitForWidget || widgetPlaceholder !== undefined;
  // What stands in for the widget until it is available.
  const waitingView = () => (
    <div
      data-testid="optio-detail-waiting"
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        width: '100%', height: '100%', minHeight: 200,
      }}
    >
      {widgetPlaceholder !== undefined
        ? widgetPlaceholder
        : Spinner ? <Spinner spinnerKey={spinnerKey} size="large" /> : <Spin size="large" />}
    </div>
  );

  if (!processId) {
    return (
      <div data-testid="optio-detail-empty" style={{ color: '#999', textAlign: 'center', marginTop: 100 }}>
        Select a process to view details
      </div>
    );
  }

  if (processNotFound) {
    return (
      <div
        data-testid="optio-detail-not-found"
        style={{ color: '#999', textAlign: 'center', marginTop: 100 }}
      >
        Process not found.
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="optio-detail-error"
        style={{ color: '#a8071a', textAlign: 'center', marginTop: 100 }}
      >
        Error while accessing process: {error.message}
      </div>
    );
  }

  if (!tree) {
    if (waiting) return waitingView();
    return (
      <div data-testid="optio-detail-loading">
        {Spinner && (
          <span style={{ marginInlineEnd: 8 }}><Spinner spinnerKey={spinnerKey} size="small" /></span>
        )}
        Loading…
      </div>
    );
  }

  if (widgetResult) {
    const { widget, mode } = widgetResult;
    return (
      <div
        data-testid="optio-widget-layout"
        style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}
      >
        {showLogs && (
          <div style={{ flex: '0 0 20%', minHeight: 0, overflow: 'hidden' }}>
            <ProcessLogPanel logs={logs} tree={tree} fillParent />
          </div>
        )}
        {mode === 'suspended' && !readOnly && (
          <div
            data-testid="optio-widget-suspended-actions"
            style={{ flex: '0 0 auto', display: 'flex', justifyContent: 'flex-end', padding: '4px 8px' }}
          >
            <LaunchControls
              process={tree}
              onLaunch={(id, opts) => launch(id, opts)}
              onLaunched={onLaunched}
              onResurrect={(id) => resurrect(id)}
            />
          </div>
        )}
        <div style={{ flex: '1 1 auto', minHeight: 0 }}>
          {widget}
        </div>
      </div>
    );
  }

  // No widget yet: a scheduled or running process will show one soon.
  if (waiting && isActive(tree)) return waitingView();

  return (
    <div data-testid="optio-detail-default">
      <ProcessTreeView
        treeData={tree}
        sseState={{ connected }}
        onLaunch={readOnly ? undefined : (id, opts) => launch(id, opts)}
        onLaunched={onLaunched}
        onResurrect={readOnly ? undefined : (id) => resurrect(id)}
        onCancel={readOnly ? undefined : (id) => cancel(id)}
      />
      {showLogs && <ProcessLogPanel logs={logs} tree={tree} />}
    </div>
  );
}
