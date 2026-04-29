/**
 * Predicates over a process document's lifecycle state and resume capability.
 *
 * The constants are intentionally not exported — callers should ask
 * "is this launchable?" rather than "is the state in this set?", so the
 * internal rule can grow (e.g., add capability checks) without API churn.
 */

const LAUNCHABLE_STATES = new Set(['idle', 'done', 'failed', 'cancelled']);
const ACTIVE_STATES = new Set(['running', 'scheduled', 'cancel_requested', 'cancelling']);

export interface ProcessStateLike {
  status?: { state?: string } | null;
  supportsResume?: boolean;
  hasSavedState?: boolean;
}

export function isLaunchable(process: ProcessStateLike | null | undefined): boolean {
  const state = process?.status?.state;
  return !!state && LAUNCHABLE_STATES.has(state);
}

export function isActive(process: ProcessStateLike | null | undefined): boolean {
  const state = process?.status?.state;
  return !!state && ACTIVE_STATES.has(state);
}

export function isResumable(process: ProcessStateLike | null | undefined): boolean {
  return process?.supportsResume === true && process?.hasSavedState === true;
}
