import { useProcessStream } from '../hooks/useProcessStream.js';
import { ProcessTreeView } from './ProcessTreeView.js';
import { ProcessLogPanel } from './ProcessLogPanel.js';
import { useProcessWidget } from './ProcessWidget.js';
import { useProcessActions } from '../hooks/useProcessActions.js';

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
}

export function ProcessDetailView({ processId, readOnly = false }: ProcessDetailViewProps) {
  const { tree, logs, connected, processNotFound, error } =
    useProcessStream(processId ?? undefined);
  const { launch, cancel } = useProcessActions();
  const widget = useProcessWidget(tree);

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
    return <div data-testid="optio-detail-loading">Loading…</div>;
  }

  if (widget) {
    return (
      <div
        data-testid="optio-widget-layout"
        style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}
      >
        <div style={{ flex: '0 0 20%', minHeight: 0, overflow: 'hidden' }}>
          <ProcessLogPanel logs={logs} tree={tree} fillParent />
        </div>
        <div style={{ flex: '1 1 auto', minHeight: 0 }}>
          {widget}
        </div>
      </div>
    );
  }

  return (
    <div data-testid="optio-detail-default">
      <ProcessTreeView
        treeData={tree}
        sseState={{ connected }}
        onLaunch={readOnly ? undefined : (id, opts) => launch(id, opts)}
        onCancel={readOnly ? undefined : (id) => cancel(id)}
      />
      <ProcessLogPanel logs={logs} tree={tree} />
    </div>
  );
}
