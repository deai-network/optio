import type { ReactNode } from 'react';
import { useOptioBaseUrl, useOptioPrefix, useOptioDatabase } from '../context/useOptioContext.js';
import { getWidget, getSuspendedWidget } from '../widgets/registry.js';
import { isWidgetLive, isSuspended } from '../process-state.js';
import type { ProcessTreeNode } from '../hooks/useProcessStream.js';

export interface UseProcessWidgetStateResult {
  widget: ReactNode;
  /** `'live'` while the task is running and its live widget is shown;
   *  `'suspended'` while the task is suspended and its suspended variant is
   *  shown (same `WidgetProps` either way). */
  mode: 'live' | 'suspended';
}

/**
 * Returns the rendered widget for a process tree, plus which mode produced it,
 * or `null` if no widget should be shown right now. Layout is the caller's
 * problem — this hook only handles the readiness gate, registry lookup, and
 * proxy-URL build.
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
 *
 * Use this (rather than `useProcessWidget`) when the caller needs to know
 * which mode produced the element — e.g. `ProcessDetailView` renders a Resume
 * control only in `'suspended'` mode. Callers that only need the element
 * itself should use `useProcessWidget`, which delegates here.
 */
export function useProcessWidgetState(
  tree: ProcessTreeNode | null | undefined,
): UseProcessWidgetStateResult | null {
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

/**
 * Backward-compatible shape: returns the widget element directly (`ReactNode
 * | null`), exactly as before suspended-widget support was added. Delegates
 * to `useProcessWidgetState` and unwraps `.widget` — so existing callers that
 * render `useProcessWidget(tree)` directly (e.g. Excavator's
 * `ProcessDetailPage`) now also get the suspended variant for free, with no
 * code change on their side, and no risk of a `{widget, mode}` object
 * reaching React's child-rendering path (which would throw "Objects are not
 * valid as a React child").
 *
 * Use `useProcessWidgetState` instead when the caller needs to know which
 * mode produced the element.
 */
export function useProcessWidget(tree: ProcessTreeNode | null | undefined): ReactNode | null {
  return useProcessWidgetState(tree)?.widget ?? null;
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
