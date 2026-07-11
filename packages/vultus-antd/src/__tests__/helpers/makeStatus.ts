import { vi } from 'vitest';
import type { ActionStatus } from 'vultus-core';

export function makeStatus(overrides: Partial<ActionStatus> = {}): ActionStatus {
  const base: ActionStatus = {
    id: 'test',
    label: 'Click',
    variant: 'default',
    pending: false,
    disabled: false,
    invisible: false,
    errors: [],
    fire: vi.fn(),
    firePromise: vi.fn(async () => {}),
  };
  return { ...base, ...overrides };
}
