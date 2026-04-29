import { useProcessStream } from '../hooks/useProcessStream.js';
import { ProcessTreeView } from './ProcessTreeView.js';
import { ProcessLogPanel } from './ProcessLogPanel.js';
import { useProcessWidget } from './ProcessWidget.js';
import { useProcessActions } from '../hooks/useProcessActions.js';

export interface ProcessDetailViewProps {
  processId: string | null | undefined;
}

export function ProcessDetailView({ processId }: ProcessDetailViewProps) {
  const { tree, logs, connected } = useProcessStream(processId ?? undefined);
  const { launch } = useProcessActions();
  const widget = useProcessWidget(tree);

  if (!processId) {
    return (
      <div data-testid="optio-detail-empty" style={{ color: '#999', textAlign: 'center', marginTop: 100 }}>
        Select a process to view details
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
          <ProcessLogPanel logs={logs} fillParent />
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
        onLaunch={(id, opts) => launch(id, opts)}
      />
      <ProcessLogPanel logs={logs} />
    </div>
  );
}
