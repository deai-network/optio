import { describe, it, expect, vi } from 'vitest';
import { routeApiError } from '../routeApiError.js';
import type { ErrorRoutesRegistry } from '../types.js';

type R = 'dataspaces.create' | 'dataspaces.delete';

const registry: ErrorRoutesRegistry<R> = {
  'dataspaces.create': {
    'slug-conflict': { i18nKey: 'errors.dataspaces.create.slug-conflict', field: 'displayName' },
  },
  'dataspaces.delete': {
    'not-found': { i18nKey: 'errors.dataspaces.delete.not-found' },
  },
};

describe('routeApiError', () => {
  it('field path: calls form.setFields when entry has field and ctx has form', () => {
    const setFields = vi.fn();
    const form = { setFields } as never;
    routeApiError(
      { status: 409, body: { message: 'conflict', reason: 'slug-conflict' } },
      { route: 'dataspaces.create', form, t: (k) => `T:${k}` },
      registry,
    );
    expect(setFields).toHaveBeenCalledWith([
      { name: 'displayName', errors: ['T:errors.dataspaces.create.slug-conflict'] },
    ]);
  });

  it('inline path: calls setInlineError when no field but inline-setter present', () => {
    const setInlineError = vi.fn();
    routeApiError(
      { status: 404, body: { message: 'gone', reason: 'not-found' } },
      { route: 'dataspaces.delete', setInlineError, t: (k) => `T:${k}` },
      registry,
    );
    expect(setInlineError).toHaveBeenCalledWith('T:errors.dataspaces.delete.not-found');
  });

  it('sink path: calls the injected showError when neither form nor inline-setter', () => {
    const showError = vi.fn();
    routeApiError(
      { status: 500, body: { message: 'fallback msg' } },
      { route: 'dataspaces.create', t: (k) => `T:${k}`, showError },
      registry,
    );
    expect(showError).toHaveBeenCalledWith('fallback msg');
  });

  it('falls back to parsed.message for unmapped reasons', () => {
    const setInlineError = vi.fn();
    routeApiError(
      { status: 409, body: { message: 'oops', reason: 'mystery-reason' } },
      { route: 'dataspaces.create', setInlineError, t: (k) => `T:${k}` },
      registry,
    );
    expect(setInlineError).toHaveBeenCalledWith('oops');
  });

  it('falls back to common.error key when nothing parseable', () => {
    const setInlineError = vi.fn();
    routeApiError(
      null,
      { route: 'dataspaces.create', setInlineError, t: (k) => `T:${k}` },
      registry,
    );
    expect(setInlineError).toHaveBeenCalledWith('T:common.error');
  });
});
