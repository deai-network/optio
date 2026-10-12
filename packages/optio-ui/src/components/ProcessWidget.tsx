import { useRef, type ReactNode } from 'react';
import { useOptioBaseUrl, useOptioPrefix, useOptioDatabase } from '../context/useOptioContext.js';
import { getWidget, getSuspendedWidget } from '../widgets/registry.js';
import { isActive, isWidgetLive, isSuspended } from '../process-state.js';
import type { ProcessTreeNode } from '../hooks/useProcessStream.js';

export interface UseProcessWidgetStateResult {
  widget: ReactNode;
  /** `'live'` while the task is running and its live widget is shown;
   *  `'suspended'` while the task is suspended and its suspended variant is
   *  shown (same `WidgetProps` either way); `'resuming'` while a process this
   *  hook was showing suspended resumes and its live widget is not ready yet:
   *  the suspended variant stays on screen (a relaunch clears `widgetData`
   *  until the task sets it again), so the host's layout does not flip. */
  mode: 'live' | 'suspended' | 'resuming';
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
export interface UseProcessWidgetOptions {
  /** Passed to the widget as `WidgetProps.readOnly`. */
  readOnly?: boolean;
  /** Passed to the widget as `WidgetProps.hideEmptyRuns`. */
  hideEmptyRuns?: boolean;
}

export function useProcessWidgetState(
  tree: ProcessTreeNode | null | undefined,
  options?: UseProcessWidgetOptions,
): UseProcessWidgetStateResult | null {
  const baseUrl = useOptioBaseUrl();
  const prefix = useOptioPrefix();
  const database = useOptioDatabase();
  // The process this hook last rendered with its suspended variant (its
  // _id), if any: set where a suspended or resuming widget is returned,
  // cleared on every other outcome.
  const shownSuspended = useRef<string | null>(null);
  const none = () => {
    shownSuspended.current = null;
    return null;
  };

  if (!tree) return none();

  const widgetName = (tree as any).uiWidget as string | undefined;
  if (!widgetName) return none();

  const live = isWidgetLive(tree as any);
  const suspended = !live && isSuspended(tree as any);
  const widgetDataReady = (tree as any).widgetData != null;
  // Resuming: this hook showed the process suspended, and it has since been
  // launched, but its live widget is not ready yet. Keep the suspended
  // variant until it is (or the run ends).
  const resuming = !suspended
    && shownSuspended.current === tree._id
    && isActive(tree as any)
    && !(live && widgetDataReady);
  if (!live && !suspended && !resuming) return none();
  if (!widgetDataReady && !resuming) return none();

  const Widget = live && !resuming ? getWidget(widgetName) : getSuspendedWidget(widgetName);
  if (!Widget) {
    if (live) {
      console.warn(`[optio-ui] No widget registered under name "${widgetName}"; falling back to default rendering.`);
    }
    return none();
  }

  if (!database) {
    console.warn(
      `[optio-ui] Widget "${widgetName}" requested but database is unknown ` +
      `(no explicit database on <OptioProvider> and instance discovery has not resolved); ` +
      `falling back to default rendering.`,
    );
    return none();
  }

  const widgetProxyUrl =
    `${baseUrl}/api/widget/${encodeURIComponent(database)}/${encodeURIComponent(prefix)}/${tree._id}/`;

  const mode = resuming ? 'resuming' : live ? 'live' : 'suspended';
  shownSuspended.current = mode === 'live' ? null : tree._id;
  return {
    mode,
    widget: (
      <Widget
        process={tree as any}
        apiBaseUrl={baseUrl}
        widgetProxyUrl={widgetProxyUrl}
        prefix={prefix}
        database={database}
        {...(options?.readOnly ? { readOnly: true } : {})}
        {...(options?.hideEmptyRuns ? { hideEmptyRuns: true } : {})}
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
export function useProcessWidget(
  tree: ProcessTreeNode | null | undefined,
  options?: UseProcessWidgetOptions,
): ReactNode | null {
  return useProcessWidgetState(tree, options)?.widget ?? null;
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
