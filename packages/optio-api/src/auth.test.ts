import { describe, it, expect } from 'vitest';
import { checkAuth, type OptioRole } from './auth.js';

describe('checkAuth', () => {
  it('returns 401 when callback returns null', async () => {
    const result = await checkAuth({}, () => null, false);
    expect(result).toEqual({ status: 401, body: { message: 'Unauthorized' } });
  });

  it('returns null for viewer on read endpoint', async () => {
    const result = await checkAuth({}, () => 'viewer', false);
    expect(result).toBeNull();
  });

  it('returns 403 for viewer on write endpoint', async () => {
    const result = await checkAuth({}, () => 'viewer', true);
    expect(result).toEqual({ status: 403, body: { message: 'Forbidden' } });
  });

  it('returns null for operator on read endpoint', async () => {
    const result = await checkAuth({}, () => 'operator', false);
    expect(result).toBeNull();
  });

  it('returns null for operator on write endpoint', async () => {
    const result = await checkAuth({}, () => 'operator', true);
    expect(result).toBeNull();
  });

  it('supports async callbacks', async () => {
    const result = await checkAuth({}, async (): Promise<OptioRole> => 'operator', true);
    expect(result).toBeNull();
  });

  it('passes the request to the callback', async () => {
    const fakeReq = { headers: { authorization: 'Bearer xyz' } };
    let receivedReq: unknown;
    await checkAuth(fakeReq, (req) => { receivedReq = req; return 'operator'; }, false);
    expect(receivedReq).toBe(fakeReq);
  });
});
