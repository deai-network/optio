import type { ReactNode } from 'react';
import { useOptioBaseUrl, useOptioPrefix, useOptioDatabase } from '../context/useOptioContext.js';
import { getWidget } from '../widgets/registry.js';
import { isWidgetLive } from '../process-state.js';
import type { ProcessTreeNode } from '../hooks/useProcessStream.js';

/**
 * Returns the rendered widget element for a process tree, or `null` if no
 * widget should be shown right now. Layout is the caller's problem — this
 * hook only handles the readiness gate, registry lookup, and proxy-URL build.
 *
 * Returns `null` when:
 *   - tree is missing,
 *   - tree.uiWidget is not set,
 *   - state is not widget-live (running | cancel_requested | cancelling),
 *   - widgetData hasn't arrived yet (the worker hasn't called
 *     `set_widget_data` — task is typically still doing pre-widget setup),
 *   - the widget name is not registered (also emits console.warn),
 *   - the database is unknown (also emits console.warn — widget URLs need it).
 */
export function useProcessWidget(tree: ProcessTreeNode | null | undefined): ReactNode | null {
  const baseUrl = useOptioBaseUrl();
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();

  if (!tree) return null;

  const widgetName = (tree as any).uiWidget as string | undefined;
  if (!widgetName) return null;

  if (!isWidgetLive(tree as any)) return null;

  const widgetDataReady = (tree as any).widgetData != null;
  if (!widgetDataReady) return null;

  const Widget = getWidget(widgetName);
  if (!Widget) {
    console.warn(`[optio-ui] No widget registered under name "${widgetName}"; falling back to default rendering.`);
    return null;
  }

  if (!database) {
    console.warn(
      `[optio-ui] Widget "${widgetName}" requested but database is unknown ` +
      `(no explicit database on <OptioProvider> and instance discovery has not resolved); ` +
      `falling back to default rendering.`,
    );
    return null;
  }

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

export interface ProcessWidgetProps {
  tree: ProcessTreeNode | null | undefined;
}

/**
 * Component wrapper around `useProcessWidget`. Renders the widget element when
 * one is ready, or `null` otherwise (no chrome, no fallback). Caller composes
 * the surrounding layout — log strip, tabs, full-bleed, whatever.
 */
export function ProcessWidget({ tree }: ProcessWidgetProps): ReactNode {
  return useProcessWidget(tree);
}
