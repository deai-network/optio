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

export function isLaunchable(process: ProcessStateLike | null | undefined): boolean {
  return isLaunchableState(process?.status?.state);
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
