export type OptioRole = 'viewer' | 'operator';

export type AuthCallback<TRequest> =
  (req: TRequest) => Promise<OptioRole | null> | OptioRole | null;

export interface AuthResult {
  status: 401 | 403;
  body: { message: string };
}

// The role each authenticated request was given, for `accessFor`. Keyed by the
// framework-native request object, so it is collected with the request.
const requestRoles = new WeakMap<object, OptioRole>();

export async function checkAuth<TRequest>(
  req: TRequest,
  authenticate: AuthCallback<TRequest>,
  isWrite: boolean,
): Promise<AuthResult | null> {
  const role = await authenticate(req);
  if (role === null) return { status: 401, body: { message: 'Unauthorized' } };
  if (isWrite && role === 'viewer') return { status: 403, body: { message: 'Forbidden' } };
  if (typeof req === 'object' && req !== null) requestRoles.set(req, role);
  return null;
}

// --- Per-request access: scope + authorize ---

/** A flat exact-match metadata map: the tenant boundary a request is confined to. */
export type ScopeFilter = Record<string, string | number | boolean | null>;

export type OptioAction =
  | 'read'
  | 'launch' | 'cancel' | 'dismiss' | 'resync'
  | 'widget' | 'widget-control' | 'widget-upload'
  | 'instances';

/** What `authorize` sees of a process. */
export interface ProcessRef {
  _id: string;
  processId: string;
  name: string;
  parentId?: string;
  rootId: string;
  metadata: Record<string, unknown>;
}

export interface AuthorizeInput {
  role: OptioRole;
  action: OptioAction;
  /** The process a single-process action targets. */
  process?: ProcessRef;
  /** resync: the effective (scoped) filter. */
  metadataFilter?: Record<string, unknown>;
  /** resync: whether process records are deleted first. */
  clean?: boolean;
}

/**
 * Confines a request to processes whose `metadata` equals every key of the
 * returned map. `null` (or an empty map) leaves the request unscoped.
 */
export type ScopeCallback<TRequest> =
  (req: TRequest) => ScopeFilter | null | Promise<ScopeFilter | null>;

/** Decides a mutating action (and instance discovery); `false` gives 403. */
export type AuthorizeCallback<TRequest> =
  (req: TRequest, input: AuthorizeInput) => boolean | Promise<boolean>;

export interface AccessHooks<TRequest> {
  scope?: ScopeCallback<TRequest>;
  authorize?: AuthorizeCallback<TRequest>;
}

/** Framework-agnostic access for one request; handlers take this. */
export interface Access {
  /** False when the host passed neither hook: handlers skip every check. */
  restricted: boolean;
  scope: ScopeFilter | null;
  authorize(input: Omit<AuthorizeInput, 'role'>): Promise<boolean>;
}

export const UNRESTRICTED: Access = Object.freeze({
  restricted: false,
  scope: null,
  authorize: async () => true,
});

export async function resolveAccess<TRequest>(
  hooks: AccessHooks<TRequest>,
  req: TRequest,
  role: OptioRole,
): Promise<Access> {
  if (!hooks.scope && !hooks.authorize) return UNRESTRICTED;
  const raw = hooks.scope ? await hooks.scope(req) : null;
  const scope = raw && Object.keys(raw).length > 0 ? raw : null;
  const authorize = hooks.authorize;
  return {
    restricted: true,
    scope,
    authorize: authorize
      ? async (input) => (await authorize(req, { ...input, role })) === true
      : async () => true,
  };
}

const requestAccess = new WeakMap<object, Promise<Access>>();

/**
 * The access of a request that already passed `checkAuth`, resolved once per
 * request. Throws (synchronously) for a request `checkAuth` never accepted:
 * that is an adapter wiring bug, and failing loudly beats guessing a role.
 */
export function accessFor<TRequest extends object>(
  req: TRequest,
  hooks: AccessHooks<TRequest>,
): Promise<Access> {
  if (!hooks.scope && !hooks.authorize) return Promise.resolve(UNRESTRICTED);
  const cached = requestAccess.get(req);
  if (cached) return cached;
  const role = requestRoles.get(req);
  if (role === undefined) {
    throw new Error('optio-api: accessFor called before checkAuth accepted the request');
  }
  const access = resolveAccess(hooks, req, role);
  requestAccess.set(req, access);
  return access;
}
