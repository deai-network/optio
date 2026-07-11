import type { ParsedApiError } from './types.js';

export function parseApiError(err: unknown): ParsedApiError {
  if (typeof err !== 'object' || err === null) return { status: 0 };
  const e = err as { status?: number; body?: { message?: string; reason?: string } };
  return {
    status: e.status ?? 0,
    reason: e.body?.reason,
    message: e.body?.message,
  };
}
