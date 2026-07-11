import { routeApiError } from '../routeApiError.js';

test('falls back to the injected showError sink (no antd) when no form/inline target', () => {
  const seen: string[] = [];
  const registry = { save: { badness: { i18nKey: 'err.bad' } } } as const;
  routeApiError(
    { status: 400, body: { reason: 'badness' } },
    { route: 'save', t: (k: string) => (k === 'err.bad' ? 'Bad thing' : k), showError: (m) => seen.push(m) },
    registry,
  );
  expect(seen).toEqual(['Bad thing']);
});

test('no sink + no target does not throw (neutral default)', () => {
  const registry = { save: {} } as const;
  expect(() =>
    routeApiError({ status: 500 }, { route: 'save', t: (k: string) => k }, registry),
  ).not.toThrow();
});
