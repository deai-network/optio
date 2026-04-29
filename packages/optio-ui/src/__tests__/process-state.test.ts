import { describe, it, expect } from 'vitest';
import { isLaunchable, isActive, isResumable } from '../process-state.js';

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
