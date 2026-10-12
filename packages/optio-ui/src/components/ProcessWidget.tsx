import type { ReactNode } from 'react';
import { useOptioBaseUrl, useOptioPrefix, useOptioDatabase } from '../context/useOptioContext.js';
import { getWidget, getSuspendedWidget } from '../widgets/registry.js';
import { isWidgetLive, isSuspended } from '../process-state.js';
import type { ProcessTreeNode } from '../hooks/useProcessStream.js';

export interface UseProcessWidgetResult {
  widget: ReactNode;
  /** `'live'` while the task is running and its live widget is shown;
   *  `'suspended'` while the task is suspended and its suspended variant is
   *  shown (same `WidgetProps` either way). */
  mode: 'live' | 'suspended';
}

/**
 * Returns the rendered widget for a process tree, or `null` if no widget
 * should be shown right now. Layout is the caller's problem — this hook only
 * handles the readiness gate, registry lookup, and proxy-URL build.
 *
 * Returns `null` when:
 *   - tree is missing,
 *   - tree.uiWidget is not set,
 *   - state is neither widget-live (running | cancel_requested | cancelling)
 *     nor suspended (`isSuspended`),
 *   - widgetData hasn't arrived yet (the worker hasn't called
 *     `set_widget_data` — task is typically still doing pre-widget setup),
 *   - the widget name has no component registered for the applicable mode
 *     (live: emits console.warn; suspended: silently falls back — a widget
 *     with no suspended variant, e.g. the iframe widget, behaves exactly as
 *     it did for any other terminal state),
 *   - the database is unknown (also emits console.warn — widget URLs need it).
 */
export function useProcessWidget(
  tree: ProcessTreeNode | null | undefined,
): UseProcessWidgetResult | null {
  const baseUrl = useOptioBaseUrl();
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();

  if (!tree) return null;

  const widgetName = (tree as any).uiWidget as string | undefined;
  if (!widgetName) return null;

  const live = isWidgetLive(tree as any);
  const suspended = !live && isSuspended(tree as any);
  if (!live && !suspended) return null;

  const widgetDataReady = (tree as any).widgetData != null;
  if (!widgetDataReady) return null;

  const Widget = live ? getWidget(widgetName) : getSuspendedWidget(widgetName);
  if (!Widget) {
    if (live) {
      console.warn(`[optio-ui] No widget registered under name "${widgetName}"; falling back to default rendering.`);
    }
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

  return {
    mode: live ? 'live' : 'suspended',
    widget: (
      <Widget
        process={tree as any}
        apiBaseUrl={baseUrl}
        widgetProxyUrl={widgetProxyUrl}
        prefix={prefix}
        database={database}
      />
    ),
  };
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
  return useProcessWidget(tree)?.widget ?? null;
}
