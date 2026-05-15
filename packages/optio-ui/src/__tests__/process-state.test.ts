import { describe, it, expect } from 'vitest';
import {
  isLaunchable,
  isLaunchableState,
  isActive,
  isActiveState,
  isTerminal,
  isTerminalState,
  isWidgetLive,
  isWidgetLiveState,
  isCancellable,
  isCancellableState,
  isResumable,
} from '../process-state.js';

describe('isWidgetLiveState', () => {
  it.each(['running', 'cancel_requested', 'cancelling'])('true for %s', (state) => {
    expect(isWidgetLiveState(state)).toBe(true);
  });
  it.each(['scheduled', 'idle', 'done', 'failed', 'cancelled'])('false for %s', (state) => {
    expect(isWidgetLiveState(state)).toBe(false);
  });
  it('scheduled is active but not widget-live', () => {
    expect(isActiveState('scheduled')).toBe(true);
    expect(isWidgetLiveState('scheduled')).toBe(false);
  });
});

describe('isWidgetLive', () => {
  it.each(['running', 'cancel_requested', 'cancelling'])('true for state=%s', (state) => {
    expect(isWidgetLive({ status: { state } })).toBe(true);
  });
  it('false for missing process / state', () => {
    expect(isWidgetLive(null)).toBe(false);
    expect(isWidgetLive({})).toBe(false);
  });
});

describe('isLaunchableState', () => {
  it.each(['idle', 'done', 'failed', 'cancelled'])('true for %s', (state) => {
    expect(isLaunchableState(state)).toBe(true);
  });
  it.each(['running', 'scheduled', 'cancel_requested', 'cancelling'])('false for %s', (state) => {
    expect(isLaunchableState(state)).toBe(false);
  });
  it('false for null/undefined/empty', () => {
    expect(isLaunchableState(null)).toBe(false);
    expect(isLaunchableState(undefined)).toBe(false);
    expect(isLaunchableState('')).toBe(false);
  });
});

describe('isActiveState', () => {
  it.each(['running', 'scheduled', 'cancel_requested', 'cancelling'])('true for %s', (state) => {
    expect(isActiveState(state)).toBe(true);
  });
  it.each(['idle', 'done', 'failed', 'cancelled'])('false for %s', (state) => {
    expect(isActiveState(state)).toBe(false);
  });
  it('false for null/undefined/empty', () => {
    expect(isActiveState(null)).toBe(false);
    expect(isActiveState(undefined)).toBe(false);
    expect(isActiveState('')).toBe(false);
  });
});

describe('isTerminalState', () => {
  it.each(['done', 'failed', 'cancelled'])('true for %s', (state) => {
    expect(isTerminalState(state)).toBe(true);
  });
  it.each(['idle', 'running', 'scheduled', 'cancel_requested', 'cancelling'])('false for %s', (state) => {
    expect(isTerminalState(state)).toBe(false);
  });
  it('idle is launchable but not terminal', () => {
    expect(isLaunchableState('idle')).toBe(true);
    expect(isTerminalState('idle')).toBe(false);
  });
});

describe('isTerminal', () => {
  it.each(['done', 'failed', 'cancelled'])('true for state=%s', (state) => {
    expect(isTerminal({ status: { state } })).toBe(true);
  });
  it('false for missing process / state', () => {
    expect(isTerminal(null)).toBe(false);
    expect(isTerminal({})).toBe(false);
  });
});

describe('isLaunchable', () => {
  it.each(['idle', 'done', 'failed', 'cancelled'])('true for state=%s', (state) => {
    expect(isLaunchable({ status: { state } })).toBe(true);
  });

  it.each(['running', 'scheduled', 'cancel_requested', 'cancelling'])('false for state=%s', (state) => {
    expect(isLaunchable({ status: { state } })).toBe(false);
  });

  it('false for null/undefined/missing state', () => {
    expect(isLaunchable(null)).toBe(false);
    expect(isLaunchable(undefined)).toBe(false);
    expect(isLaunchable({})).toBe(false);
    expect(isLaunchable({ status: {} })).toBe(false);
    expect(isLaunchable({ status: null })).toBe(false);
  });

  it('false for unknown state', () => {
    expect(isLaunchable({ status: { state: 'who-knows' } })).toBe(false);
  });
});

describe('isActive', () => {
  it.each(['running', 'scheduled', 'cancel_requested', 'cancelling'])('true for state=%s', (state) => {
    expect(isActive({ status: { state } })).toBe(true);
  });

  it.each(['idle', 'done', 'failed', 'cancelled'])('false for state=%s', (state) => {
    expect(isActive({ status: { state } })).toBe(false);
  });

  it('false for null/undefined/missing state', () => {
    expect(isActive(null)).toBe(false);
    expect(isActive(undefined)).toBe(false);
    expect(isActive({})).toBe(false);
  });
});

describe('isCancellableState', () => {
  it.each(['scheduled', 'running'])('true for %s', (state) => {
    expect(isCancellableState(state)).toBe(true);
  });
  it.each(['idle', 'cancel_requested', 'cancelling', 'done', 'failed', 'cancelled'])(
    'false for %s',
    (state) => {
      expect(isCancellableState(state)).toBe(false);
    },
  );
  it('false for null/undefined/empty', () => {
    expect(isCancellableState(null)).toBe(false);
    expect(isCancellableState(undefined)).toBe(false);
    expect(isCancellableState('')).toBe(false);
  });
  it('cancellable ⊂ active (excludes cancel_requested/cancelling/scheduled overlap noted in header)', () => {
    expect(isActiveState('cancel_requested')).toBe(true);
    expect(isCancellableState('cancel_requested')).toBe(false);
    expect(isActiveState('cancelling')).toBe(true);
    expect(isCancellableState('cancelling')).toBe(false);
  });
});

describe('isCancellable', () => {
  it('true only when state is cancellable AND cancellable flag is true', () => {
    expect(isCancellable({ status: { state: 'running' }, cancellable: true })).toBe(true);
    expect(isCancellable({ status: { state: 'scheduled' }, cancellable: true })).toBe(true);
  });

  it('false when state is not cancellable, regardless of flag', () => {
    expect(isCancellable({ status: { state: 'cancelling' }, cancellable: true })).toBe(false);
    expect(isCancellable({ status: { state: 'cancel_requested' }, cancellable: true })).toBe(false);
    expect(isCancellable({ status: { state: 'done' }, cancellable: true })).toBe(false);
    expect(isCancellable({ status: { state: 'idle' }, cancellable: true })).toBe(false);
  });

  it('false when cancellable flag is false or missing, regardless of state', () => {
    expect(isCancellable({ status: { state: 'running' }, cancellable: false })).toBe(false);
    expect(isCancellable({ status: { state: 'running' } })).toBe(false);
  });

  it('false for null/undefined/empty process', () => {
    expect(isCancellable(null)).toBe(false);
    expect(isCancellable(undefined)).toBe(false);
    expect(isCancellable({})).toBe(false);
  });

  it('truthy non-boolean cancellable does not count as true', () => {
    expect(
      isCancellable({
        status: { state: 'running' },
        cancellable: 1 as unknown as boolean,
      }),
    ).toBe(false);
  });
});

describe('isResumable', () => {
  it('true only when both supportsResume and hasSavedState are true', () => {
    expect(isResumable({ supportsResume: true, hasSavedState: true })).toBe(true);
  });

  it('false when either flag is missing or false', () => {
    expect(isResumable({ supportsResume: true, hasSavedState: false })).toBe(false);
    expect(isResumable({ supportsResume: false, hasSavedState: true })).toBe(false);
    expect(isResumable({ supportsResume: true })).toBe(false);
    expect(isResumable({ hasSavedState: true })).toBe(false);
    expect(isResumable({})).toBe(false);
  });

  it('false for null/undefined', () => {
    expect(isResumable(null)).toBe(false);
    expect(isResumable(undefined)).toBe(false);
  });

  it('truthy non-boolean values do not count as true', () => {
    // Defensive: only literal `true` qualifies.
    expect(isResumable({ supportsResume: 1 as unknown as boolean, hasSavedState: 1 as unknown as boolean })).toBe(false);
  });
});
