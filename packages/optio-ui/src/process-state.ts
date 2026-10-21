/**
 * Predicates over a process document's lifecycle state and resume capability.
 *
 * **This file is the single source of truth for task-state decisions in optio-ui.**
 * If you find yourself testing `state === 'running'`, `state in [...]`, or
 * defining another `ACTIVE_STATES`/`LAUNCHABLE_STATES` constant elsewhere,
 * stop and add (or use) a predicate here instead. See the package AGENTS.md
 * for the rule.
 *
 * The state-set constants are intentionally not exported — callers should ask
 * "is this launchable?" rather than "is the state in this set?", so the
 * internal rule can grow (e.g., add capability checks) without API churn.
 *
 * Two flavours:
 *   - `is*State(state)`         — for callers that only have the raw state string.
 *   - `is*(process)`            — convenience over a process-shaped object.
 *
 * Lifecycle vocabulary (mutually exclusive groups):
 *   - launchable: idle | done | failed | cancelled  — eligible to (re)start.
 *                 `isLaunchable(process)` also leaves out a task factory's
 *                 launch (`isTaskFactoryLaunch`), which is never launched again.
 *   - active:     running | scheduled | cancel_requested | cancelling  — alive / in-flight.
 *   - terminal:   done | failed | cancelled  — finished, will not progress further.
 *   - widget-live: running | cancel_requested | cancelling  — task code is up and
 *                  the widget upstream is registered/torn-down on the same edges.
 *                  Widget-live ⊂ active (it excludes `scheduled`, where the task
 *                  hasn't started and the upstream doesn't yet exist).
 *   - cancellable: scheduled | running  — engine will accept a cancel request.
 *                  Cancellable ⊂ active. Excludes `cancel_requested`/`cancelling`
 *                  (cancel already in flight, re-request is a no-op) and all
 *                  terminal states. Mirrors optio-core's authoritative
 *                  CANCELLABLE_STATES in state_machine.py.
 *   - suspended:  cancelled AND supportsResume AND hasSavedState — a task
 *                 whose saved conversation can be read while it is not
 *                 running. Suspended ⊂ terminal ⊂ launchable. Not every
 *                 `cancelled` process is suspended (a plain cancel without
 *                 saved state is just terminal).
 *
 * `idle` is launchable but not terminal (never run yet).
 * Terminal ⊂ launchable.
 */

const LAUNCHABLE_STATES = new Set(['idle', 'done', 'failed', 'cancelled']);
const ACTIVE_STATES = new Set(['running', 'scheduled', 'cancel_requested', 'cancelling']);
const TERMINAL_STATES = new Set(['done', 'failed', 'cancelled']);
const WIDGET_LIVE_STATES = new Set(['running', 'cancel_requested', 'cancelling']);
const CANCELLABLE_STATES = new Set(['scheduled', 'running']);

export interface ProcessStateLike {
  status?: { state?: string } | null;
  cancellable?: boolean;
  supportsResume?: boolean;
  hasSavedState?: boolean;
  supportsResurrect?: boolean;
  hasUnsavedWork?: boolean;
  resurrecting?: boolean;
  autoResumeScheduled?: boolean;
  taskFactory?: boolean;
  metadata?: Record<string, unknown> | null;
}

export function isLaunchableState(state: string | null | undefined): boolean {
  return !!state && LAUNCHABLE_STATES.has(state);
}

export function isActiveState(state: string | null | undefined): boolean {
  return !!state && ACTIVE_STATES.has(state);
}

export function isTerminalState(state: string | null | undefined): boolean {
  return !!state && TERMINAL_STATES.has(state);
}

export function isWidgetLiveState(state: string | null | undefined): boolean {
  return !!state && WIDGET_LIVE_STATES.has(state);
}

export function isCancellableState(state: string | null | undefined): boolean {
  return !!state && CANCELLABLE_STATES.has(state);
}

/** In a launchable state, and not a task factory's launch (which ran once,
 *  with its values: optio-core refuses to launch it again, `not-launchable`). */
export function isLaunchable(process: ProcessStateLike | null | undefined): boolean {
  return isLaunchableState(process?.status?.state) && !isTaskFactoryLaunch(process);
}

export function isActive(process: ProcessStateLike | null | undefined): boolean {
  return isActiveState(process?.status?.state);
}

export function isTerminal(process: ProcessStateLike | null | undefined): boolean {
  return isTerminalState(process?.status?.state);
}

export function isWidgetLive(process: ProcessStateLike | null | undefined): boolean {
  return isWidgetLiveState(process?.status?.state);
}

export function isCancellable(process: ProcessStateLike | null | undefined): boolean {
  return process?.cancellable === true && isCancellableState(process?.status?.state);
}

export function isResumable(process: ProcessStateLike | null | undefined): boolean {
  return process?.supportsResume === true && process?.hasSavedState === true;
}

/** A resurrect is saving the unsaved work right now (the state stays as it
 *  was, typically `failed`; launching is locked until it ends). */
export function isResurrecting(process: ProcessStateLike | null | undefined): boolean {
  return process?.resurrecting === true;
}

/** The host still holds a failed run's unsaved work and the task can save it
 *  (and no resurrect is already saving it). */
export function isResurrectable(process: ProcessStateLike | null | undefined): boolean {
  return isLaunchable(process)
    && !isResurrecting(process)
    && process?.supportsResurrect === true
    && process?.hasUnsavedWork === true;
}

/** A cancelled task with a resumable, saved conversation: read-only view is
 *  available without launching anything. Not every `cancelled` process
 *  qualifies — a plain cancel without saved state is just terminal. */
export function isSuspended(process: ProcessStateLike | null | undefined): boolean {
  return process?.status?.state === 'cancelled'
    && process?.supportsResume === true
    && process?.hasSavedState === true;
}

/** A task factory's record: it never runs itself; its launch takes the values
 *  of its parameters (TaskFactoryLaunchModal) and makes a child that runs. */
export function isTaskFactory(process: ProcessStateLike | null | undefined): boolean {
  return process?.taskFactory === true;
}

/** A task factory's launch, the child its launch form made: its metadata
 *  names the factory (`taskFactory: <factory id>`). It ran once, with its
 *  values, and is never launched again (`isLaunchable` is false), whatever
 *  its state; a TTL keeps it for reading only. */
export function isTaskFactoryLaunch(process: ProcessStateLike | null | undefined): boolean {
  const factory = process?.metadata?.taskFactory;
  return typeof factory === 'string' && factory !== '';
}
