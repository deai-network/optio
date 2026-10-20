import { describe, it, expect } from 'vitest';
import { apiToFrontendRouteErrorReasons } from '../route-error-reasons.js';

describe('apiToFrontendRouteErrorReasons', () => {
  it('lists processes.launch with the task-factory reasons', () => {
    expect(apiToFrontendRouteErrorReasons['processes.launch']).toEqual(
      expect.arrayContaining(['parameters-required', 'not-a-factory', 'invalid-parameters']),
    );
  });

  it('lists processes.resurrect with exactly the resurrect failure reasons', () => {
    expect(apiToFrontendRouteErrorReasons['processes.resurrect']).toEqual([
      'not-found', 'not-resurrectable', 'no-resurrect-support',
      'resurrect-in-progress', 'launch-blocked', 'shutting-down',
    ]);
  });
});
