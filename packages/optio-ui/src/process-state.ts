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
 *
 * `idle` is launchable but not terminal (never run yet).
 * Terminal ⊂ launchable.
 */

const LAUNCHABLE_STATES = new Set(['idle', 'done', 'failed', 'cancelled']);
const ACTIVE_STATES = new Set(['running', 'scheduled', 'cancel_requested', 'cancelling']);
const TERMINAL_STATES = new Set(['done', 'failed', 'cancelled']);

export interface ProcessStateLike {
  status?: { state?: string } | null;
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

export function isLaunchable(process: ProcessStateLike | null | undefined): boolean {
  return isLaunchableState(process?.status?.state);
}

export function isActive(process: ProcessStateLike | null | undefined): boolean {
  return isActiveState(process?.status?.state);
}

export function isTerminal(process: ProcessStateLike | null | undefined): boolean {
  return isTerminalState(process?.status?.state);
}

export function isResumable(process: ProcessStateLike | null | undefined): boolean {
  return process?.supportsResume === true && process?.hasSavedState === true;
}
