import { useProcessStream } from '../hooks/useProcessStream.js';
import { useOptioBaseUrl, useOptioPrefix, useOptioDatabase } from '../context/useOptioContext.js';
import { getWidget } from '../widgets/registry.js';
import { ProcessTreeView } from './ProcessTreeView.js';
import { ProcessLogPanel } from './ProcessLogPanel.js';

export interface ProcessDetailViewProps {
  processId: string | null | undefined;
}

// Widget is shown only while the task is still alive. Terminal (done/failed/
// cancelled) and not-yet-started (idle/scheduled) states revert to the default
// view — the upstream is either not yet registered or already torn down, so a
// widget here would just present a broken iframe.
const WIDGET_LIVE_STATES = new Set(['running', 'cancel_requested', 'cancelling']);

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
  const currentState = (tree as any).status?.state as string | undefined;
  // Wait for the worker's set_widget_data call before swapping in the
  // widget layout.  Before widgetData exists the task is typically still
  // doing pre-widget setup work (e.g. optio-opencode uploading the ~150 MB
  // opencode binary) and we want the default tree + log view to stay
  // visible so the process progress bar + log messages are readable.
  // Once widgetData appears, we switch to the widget layout.
  const widgetDataReady = (tree as any).widgetData != null;
  if (
    widgetName
    && currentState
    && WIDGET_LIVE_STATES.has(currentState)
    && widgetDataReady
  ) {
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
        <div
          data-testid="optio-widget-layout"
          style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}
        >
          <div style={{ flex: '0 0 20%', minHeight: 0, overflow: 'hidden' }}>
            <ProcessLogPanel logs={logs} fillParent />
          </div>
          <div style={{ flex: '1 1 auto', minHeight: 0 }}>
            <Widget
              process={tree as any}
              apiBaseUrl={baseUrl}
              widgetProxyUrl={widgetProxyUrl}
              prefix={prefix}
              database={database}
            />
          </div>
        </div>
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
