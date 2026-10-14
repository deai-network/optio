import { useCallback, useEffect, useReducer, useRef, type CSSProperties, type ReactNode } from 'react';
import { useOptioBaseUrl, useOptioPrefix, useOptioDatabase } from '../context/useOptioContext.js';
import { getWidget, getSuspendedWidget, type WidgetComponent } from '../widgets/registry.js';
import { isActive, isWidgetLive, isSuspended } from '../process-state.js';
import type { ProcessTreeNode } from '../hooks/useProcessStream.js';

export interface UseProcessWidgetStateResult {
  widget: ReactNode;
  /** `'live'` while the task is running and its live widget is shown;
   *  `'suspended'` while the task is suspended and its suspended variant is
   *  shown (same `WidgetProps` either way); `'resuming'` while a process this
   *  hook was showing suspended resumes and its live widget is not ready yet:
   *  the suspended variant stays on screen (a relaunch clears `widgetData`
   *  until the task sets it again), so the host's layout does not flip;
   *  `'waiting'` (only with the `placeholder` option) while an active process
   *  has no widget to show yet: `widget` is the placeholder. */
  mode: 'live' | 'suspended' | 'resuming' | 'waiting';
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
  /** What stands in for the widget until it can show the process: returned
   *  as the widget (mode `'waiting'`) while an active process that names a
   *  registered widget has none to show yet (scheduled, or running before
   *  its `widgetData`), and passed to the widget as `WidgetProps.placeholder`
   *  for its own loading states (e.g. the suspended conversation's fetch). A
   *  resume keeps the suspended variant instead (`'resuming'`). Absent: null
   *  while waiting, and the widget's own loading indicator. */
  placeholder?: ReactNode;
}

// How long a resumed process's live widget may stay hidden without calling
// onReady before it is shown anyway (a safety net: the conversation widget
// always reports; docs/2026-10-14-conversation-backlog-batch-design.md, section 4).
const HANDOVER_TIMEOUT_MS = 10_000;

type SlotKey = 'suspended' | 'live' | 'placeholder';
interface Slot { key: SlotKey; node: ReactNode; shown: boolean }

const SHOWN: CSSProperties = { display: 'contents' };
const HIDDEN: CSSProperties = { display: 'none' };

/** The hook's widgets in keyed slots: a shown slot adds no box (the host's
 *  layout is as without it), a hidden one is out of layout, inert and out of
 *  the accessibility tree. The keys keep a widget mounted while its slot goes
 *  from hidden to shown. */
function WidgetSlots({ slots }: { slots: Slot[] }) {
  return (
    <>
      {slots.map((slot) => (
        <div
          key={slot.key}
          style={slot.shown ? SHOWN : HIDDEN}
          {...(slot.shown ? {} : { inert: true, 'aria-hidden': true })}
        >
          {slot.node}
        </div>
      ))}
    </>
  );
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
  // The resume handover: which handover is on (its process and a generation,
  // so a stale onReady from an earlier one is ignored), and whether its live
  // widget has been released (onReady, or the safety net).
  const handover = useRef<{ id: string; gen: number } | null>(null);
  const handoverGen = useRef(0);
  const released = useRef(false);
  const [, rerender] = useReducer((n: number) => n + 1, 0);
  const release = useCallback((id: string, gen: number) => {
    const on = handover.current;
    if (!on || on.id !== id || on.gen !== gen || released.current) return;
    released.current = true;
    rerender();
  }, []);

  const result = decideWidget();
  const pending = handover.current && !released.current ? handover.current : null;
  const pendingId = pending?.id ?? null;
  const pendingGen = pending?.gen ?? 0;
  useEffect(() => {
    if (pendingId === null) return;
    const timer = setTimeout(() => release(pendingId, pendingGen), HANDOVER_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [pendingId, pendingGen, release]);
  return result;

  function endHandover() {
    handover.current = null;
    released.current = false;
  }

  function decideWidget(): UseProcessWidgetStateResult | null {
    const none = () => {
      shownSuspended.current = null;
      endHandover();
      return null;
    };

    if (!tree) return none();

    const widgetName = (tree as any).uiWidget as string | undefined;
    if (!widgetName) return none();

    const live = isWidgetLive(tree as any);
    const suspended = !live && isSuspended(tree as any);
    const widgetDataReady = (tree as any).widgetData != null;
    const latched = !suspended && shownSuspended.current === tree._id && isActive(tree as any);
    // Resuming: this hook showed the process suspended, and it has since been
    // launched, but its live widget is not ready yet. Keep the suspended
    // variant until it is (or the run ends).
    const resuming = latched && !(live && widgetDataReady);
    // Handing over: the live widget is available, but mounts hidden beside the
    // suspended variant until it reports ready (or the safety net fires).
    const handingOver = latched && live && widgetDataReady;
    // On its way: an active process whose widget is not ready yet gets the
    // caller's placeholder, if the widget can show once it is.
    if (!latched && isActive(tree as any) && !(live && widgetDataReady)) {
      if (options?.placeholder === undefined || !getWidget(widgetName)) return none();
      shownSuspended.current = null;
      endHandover();
      return { mode: 'waiting', widget: <WidgetSlots slots={[{ key: 'placeholder', node: options.placeholder, shown: true }]} /> };
    }
    if (!live && !suspended && !resuming && !handingOver) return none();
    if (!widgetDataReady && !resuming) return none();

    const Live = getWidget(widgetName);
    const Suspended = getSuspendedWidget(widgetName);
    const Widget = live && !resuming && !handingOver ? Live : Suspended;
    if (!Widget || (handingOver && !Live)) {
      if (live && !resuming) {
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
    const element = (Component: WidgetComponent, extra?: { onReady: () => void }) => (
      <Component
        process={tree as any}
        apiBaseUrl={baseUrl}
        widgetProxyUrl={widgetProxyUrl}
        prefix={prefix}
        database={database}
        {...(options?.readOnly ? { readOnly: true } : {})}
        {...(options?.hideEmptyRuns ? { hideEmptyRuns: true } : {})}
        {...(options?.placeholder !== undefined ? { placeholder: options.placeholder } : {})}
        {...extra}
      />
    );

    if (handingOver) {
      const id = tree._id;
      if (!handover.current || handover.current.id !== id) {
        handoverGen.current += 1;
        handover.current = { id, gen: handoverGen.current };
        released.current = false;
      }
      if (!released.current) {
        const { gen } = handover.current;
        return {
          mode: 'resuming',
          widget: (
            <WidgetSlots
              slots={[
                { key: 'suspended', node: element(Suspended!), shown: true },
                { key: 'live', node: element(Live!, { onReady: () => release(id, gen) }), shown: false },
              ]}
            />
          ),
        };
      }
      // Released: the live widget takes over; the latch is spent.
      shownSuspended.current = null;
      endHandover();
      return { mode: 'live', widget: <WidgetSlots slots={[{ key: 'live', node: element(Live!), shown: true }]} /> };
    }

    const mode = resuming ? 'resuming' : live ? 'live' : 'suspended';
    shownSuspended.current = mode === 'live' ? null : tree._id;
    endHandover();
    const key: SlotKey = mode === 'live' ? 'live' : 'suspended';
    return { mode, widget: <WidgetSlots slots={[{ key, node: element(Widget), shown: true }]} /> };
  }
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
