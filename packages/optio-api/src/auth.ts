export type OptioRole = 'viewer' | 'operator';

export type AuthCallback<TRequest> =
  (req: TRequest) => Promise<OptioRole | null> | OptioRole | null;

export interface AuthResult {
  status: 401 | 403;
  body: { message: string };
}

export async function checkAuth<TRequest>(
  req: TRequest,
  authenticate: AuthCallback<TRequest> | undefined,
  isWrite: boolean,
): Promise<AuthResult | null> {
  if (!authenticate) return null;
  const role = await authenticate(req);
  if (role === null) return { status: 401, body: { message: 'Unauthorized' } };
  if (isWrite && role === 'viewer') return { status: 403, body: { message: 'Forbidden' } };
  return null;
}
