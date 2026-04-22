import { useProcessStream } from '../hooks/useProcessStream.js';
import { useOptioBaseUrl, useOptioPrefix, useOptioDatabase } from '../context/useOptioContext.js';
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
  const database = useOptioDatabase();

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
    if (!Widget) {
      console.warn(`[optio-ui] No widget registered under name "${widgetName}"; falling back to default rendering.`);
    } else if (!database) {
      // Widget URLs embed the Mongo database and prefix as path segments so the
      // reverse-proxy can resolve upstreams in multi-db deployments. Without
      // `database` we cannot build a correct URL; fall back to default rendering.
      console.warn(
        `[optio-ui] Widget "${widgetName}" requested but database is unknown ` +
        `(no explicit database on <OptioProvider> and instance discovery has not resolved); ` +
        `falling back to default rendering.`,
      );
    } else {
      const widgetProxyUrl =
        `${baseUrl}/api/widget/${encodeURIComponent(database)}/${encodeURIComponent(prefix)}/${tree._id}/`;
      return (
        <Widget
          process={tree as any}
          apiBaseUrl={baseUrl}
          widgetProxyUrl={widgetProxyUrl}
          prefix={prefix}
          database={database}
        />
      );
    }
  }

  return (
    <div data-testid="optio-detail-default">
      <ProcessTreeView treeData={tree} sseState={{ connected }} />
      <ProcessLogPanel logs={logs} />
    </div>
  );
}
