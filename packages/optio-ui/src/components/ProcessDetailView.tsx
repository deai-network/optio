import { useProcessStream } from '../hooks/useProcessStream.js';
import { useOptioBaseUrl, useOptioPrefix } from '../context/useOptioContext.js';
import { getWidget } from '../widgets/registry.js';
import { ProcessTreeView } from './ProcessTreeView.js';
import { ProcessLogPanel } from './ProcessLogPanel.js';

export interface ProcessDetailViewProps {
  processId: string | null | undefined;
}

export function ProcessDetailView({ processId }: ProcessDetailViewProps) {
  const { tree, logs, connected } = useProcessStream(processId ?? undefined);
  const baseUrl = useOptioBaseUrl();
  const prefix = useOptioPrefix();

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

  const widgetName = (tree as any).uiWidget as string | undefined;
  if (widgetName) {
    const Widget = getWidget(widgetName);
    if (Widget) {
      const widgetProxyUrl = `${baseUrl}/api/widget/${tree._id}/`;
      return (
        <Widget
          process={tree as any}
          apiBaseUrl={baseUrl}
          widgetProxyUrl={widgetProxyUrl}
          prefix={prefix}
        />
      );
    }
    console.warn(`[optio-ui] No widget registered under name "${widgetName}"; falling back to default rendering.`);
  }

  return (
    <div data-testid="optio-detail-default">
      <ProcessTreeView treeData={tree} sseState={{ connected }} />
      <ProcessLogPanel logs={logs} />
    </div>
  );
}
