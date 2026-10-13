import { normalizeVisibility } from './visibility.js';
import type { ProcessMetadataFilter } from './types.js';

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
  | 'launch' | 'cancel' | 'dismiss' | 'resurrect' | 'resync'
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

/**
 * Which processes this request may know about: a metadata predicate in
 * optio's filter language (the same one `metadataFilter` uses), resolved
 * once per request, together with `scope`. `null`, `undefined` and `{}`
 * mean no restriction. A missing metadata key follows MongoDB's rules: a
 * leaf like `eq('ownerId', me)` does not match it (fails closed), while
 * `ne`, `notIn` and `not(...)` do. `eq(key, null)` matches a *missing* key
 * as well as a null one (also MongoDB semantics) -- so a null or undefined
 * user id must never reach the predicate; pass `scope`/deny access instead
 * of building a leaf from one. An invalid predicate fails the request,
 * including an `undefined` value anywhere in it (rejected outright, never
 * normalized to "no restriction" the way the top-level `null` is).
 */
export type VisibilityCallback<TRequest> =
  (req: TRequest) => ProcessMetadataFilter | null | Promise<ProcessMetadataFilter | null>;

/**
 * Decides what a request may do to a process it can see (actions, widget
 * access, the saved conversation), plus resync and instance discovery;
 * `false` gives 403. A process the request cannot see is a 404 before
 * `authorize` is asked.
 */
export type AuthorizeCallback<TRequest> =
  (req: TRequest, input: AuthorizeInput) => boolean | Promise<boolean>;

/** The three optional per-request hooks a host may pass next to
 *  `authenticate`: `scope` (tenant boundary), `visibility` (which processes
 *  within the tenant may be seen) and `authorize` (which actions a seen
 *  process allows). Each is independent; passing none leaves every request
 *  `UNRESTRICTED`, with no extra lookups. */
export interface AccessHooks<TRequest> {
  scope?: ScopeCallback<TRequest>;
  visibility?: VisibilityCallback<TRequest>;
  authorize?: AuthorizeCallback<TRequest>;
}

/** True when any of `scope`, `visibility` or `authorize` is set. */
export function hasAccessHooks<TRequest>(hooks: AccessHooks<TRequest>): boolean {
  return !!(hooks.scope || hooks.visibility || hooks.authorize);
}

/** Framework-agnostic access for one request; handlers take this. */
export interface Access {
  /** False when the host passed no hook: handlers skip every check. */
  restricted: boolean;
  scope: ScopeFilter | null;
  /** The resolved, normalized `visibility` predicate for this request
   *  (`normalizeVisibility`'s result); `null` means no restriction. A
   *  hand-built `Access` (custom adapters, tests) must set this explicitly
   *  -- there is no default. */
  visibility: ProcessMetadataFilter | null;
  authorize(input: Omit<AuthorizeInput, 'role'>): Promise<boolean>;
}

export const UNRESTRICTED: Access = Object.freeze({
  restricted: false,
  scope: null,
  visibility: null,
  authorize: async () => true,
});

export async function resolveAccess<TRequest>(
  hooks: AccessHooks<TRequest>,
  req: TRequest,
  role: OptioRole,
): Promise<Access> {
  if (!hasAccessHooks(hooks)) return UNRESTRICTED;
  const raw = hooks.scope ? await hooks.scope(req) : null;
  const scope = raw && Object.keys(raw).length > 0 ? raw : null;
  const visibility = normalizeVisibility(hooks.visibility ? await hooks.visibility(req) : null);
  const authorize = hooks.authorize;
  return {
    restricted: true,
    scope,
    visibility,
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
  if (!hasAccessHooks(hooks)) return Promise.resolve(UNRESTRICTED);
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
