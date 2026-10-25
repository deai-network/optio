// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { createNextHandler } from '@ts-rest/serverless/next';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { Db } from 'mongodb';
import type { MongoClient } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';
import { discoverInstances } from '../discovery.js';
import { resolveDb, type DbOptions } from '../resolve.js';
import type { AuthCallback } from '../auth.js';
import { AsyncLocalStorage } from 'node:async_hooks';
import { Readable } from 'node:stream';
import { resultQueryOf } from '../result-download.js';
import {
  accessFor, checkAuth, hasAccessHooks, launchStamp, UNRESTRICTED,
  type Access, type AccessHooks, type AuthorizeCallback, type LaunchMetadataCallback, type ScopeCallback,
  type VisibilityCallback,
} from '../auth.js';
import { gateInstances, inScope } from '../access-scope.js';
import { processReads } from '../process-reads.js';
import { isWriteMethod } from '../widget-proxy-core.js';
import {
  detectLegacyMetadataParams,
  formatLegacyMetadataMessage,
} from '../metadata-filter-query.js';
import {
  parseSseOptions,
  checkLegacyMetadataParams,
  LegacyMetadataParamError,
} from '../sse-options.js';
import { createOptioContext, type OptioContext } from '../context.js';

export interface OptioRouteHandlers {
  GET: (request: Request) => Promise<Response>;
  POST: (request: Request) => Promise<Response>;
  /** Present only when called with sugar-form options (caller did not supply ctx). */
  ctx?: OptioContext;
}

export type OptioApiOptions =
  | (BaseOptioApiOptions & { ctx: OptioContext })
  | (BaseOptioApiOptions & { redis: Redis } & (
      | { db: Db; mongoClient?: never }
      | { mongoClient: MongoClient; db?: never }
    ));

interface BaseOptioApiOptions {
  prefix?: string;
  authenticate: AuthCallback<Request>;
  /** Confine each request to a tenant's processes (see auth.ts). */
  scope?: ScopeCallback<Request>;
  /** Decide mutating actions and instance discovery (see auth.ts). */
  authorize?: AuthorizeCallback<Request>;
  /** Hide processes from a request (see auth.ts). */
  visibility?: VisibilityCallback<Request>;
  /** Stamp a task factory's launch, server-side (see auth.ts); not an access hook. */
  launchMetadata?: LaunchMetadataCallback<Request>;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function createOptioRouteHandlers(opts: OptioApiOptions): OptioRouteHandlers {
  const explicit = 'ctx' in opts;
  let ctx: OptioContext;
  let redis: Redis;
  let dbOpts: DbOptions;
  if (explicit) {
    ctx = opts.ctx;
    dbOpts = ctx.dbOpts;
    redis = ctx.redis;
  } else {
    redis = opts.redis;
    dbOpts = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };
    ctx = createOptioContext({ dbOpts, redis });
  }

  // ts-rest's app-router handler sees its own request object, not the
  // caller's, so the request's access, and the caller's request itself (for
  // `launchMetadata`), travel to the routes in an AsyncLocalStorage set around
  // the ts-rest call.
  const hooks: AccessHooks<Request> = {
    scope: opts.scope, authorize: opts.authorize, visibility: opts.visibility,
  };
  const requestAccess = new AsyncLocalStorage<{ access: Access; request: Request }>();
  function currentAccess(): Access {
    const store = requestAccess.getStore();
    if (store) return store.access;
    if (!hasAccessHooks(hooks)) return UNRESTRICTED;
    throw new Error('optio-api: route reached without the request\'s access');
  }
  function currentRequest(): Request {
    const store = requestAccess.getStore();
    if (store) return store.request;
    throw new Error('optio-api: route reached without the caller\'s request');
  }

  async function authGate(request: Request): Promise<Response | null> {
    const authResult = await checkAuth(request, opts.authenticate, isWriteMethod(request.method));
    if (!authResult) return null;
    return new Response(JSON.stringify(authResult.body), {
      status: authResult.status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const tsRestHandlers = createNextHandler(
    apiContract.processes,
    {
      list: async ({ query }) => {
        const result = await handlers.listProcesses(ctx, query, await currentAccess());
        return { status: 200 as const, body: result };
      },
      get: async ({ params, query }) => {
        const result = await handlers.getProcess(ctx, query, params.id, await currentAccess());
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getTree: async ({ params, query }) => {
        const result = await handlers.getProcessTree(ctx, query, params.id, await currentAccess());
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getLog: async ({ params, query }) => {
        const result = await handlers.getProcessLog(ctx, query, params.id, await currentAccess());
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getTreeLog: async ({ params, query }) => {
        const result = await handlers.getProcessTreeLog(ctx, query, params.id, await currentAccess());
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getConversation: async ({ params, query }, { responseHeaders }) => {
        const { headers, ...result } = await handlers.getProcessConversation(
          ctx, query, params.id, await currentAccess(),
        );
        for (const [name, value] of Object.entries(headers)) responseHeaders.set(name, value);
        return result as any;
      },
      launch: async ({ params, query, body }) => {
        const result = await handlers.launchProcess(
          ctx, query, params.id, body?.resume === true, null, await currentAccess(), body?.params, body?.ephemeral,
          opts.launchMetadata && launchStamp(opts.launchMetadata, currentRequest(), body?.params),
        );
        return result as any;
      },
      cancel: async ({ params, query }) => {
        const result = await handlers.cancelProcess(ctx, query, params.id, await currentAccess());
        return result as any;
      },
      dismiss: async ({ params, query }) => {
        const result = await handlers.dismissProcess(ctx, query, params.id, await currentAccess());
        return result as any;
      },
      resurrect: async ({ params, query, body }) => {
        const result = await handlers.resurrectProcess(
          ctx, query, params.id, body?.sessionId ?? null, await currentAccess(),
        );
        return result as any;
      },
      resync: async ({ query, body }: { query: { database?: string; prefix?: string }; body: { clean?: boolean; metadataFilter?: import('../types.js').ProcessMetadataFilter } }) => {
        const result = await handlers.resyncProcesses(
          ctx, query, body.clean, body.metadataFilter, await currentAccess(),
        );
        return result as any;
      },
    },
    { handlerType: 'app-router' },
  );

  async function GET(request: Request): Promise<Response> {
    const denied = await authGate(request);
    if (denied) return denied;
    const url = new URL(request.url);
    const { pathname } = url;

    // Discovery: /api/optio/instances
    const access = await accessFor(request, hooks);

    if (pathname === '/api/optio/instances') {
      if (!(await gateInstances(access))) {
        return new Response(JSON.stringify({ message: 'Forbidden' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const instances = await discoverInstances(dbOpts, redis, opts.prefix);
      return new Response(JSON.stringify({ instances }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Tree stream: /api/processes/<id>/tree/stream
    const treeStreamMatch = pathname.match(/^\/api\/processes\/([^/]+)\/tree\/stream$/);
    if (treeStreamMatch) {
      const id = treeStreamMatch[1];
      const rawQuery = Object.fromEntries(url.searchParams.entries());
      let sseOpts;
      try {
        sseOpts = parseSseOptions(rawQuery);
      } catch (e) {
        return new Response(JSON.stringify({ message: (e as Error).message }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const { db, prefix: resolvedPrefix } = resolveDb(dbOpts, sseOpts);

      const reads = processReads(db, resolvedPrefix, access.visibility);
      const found = await reads.findOne({ _id: new ObjectId(id) });
      const proc = found && (await inScope(reads, found, access.scope)) ? found : null;
      if (!proc) {
        return new Response(JSON.stringify({ message: 'Process not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      const stream = new ReadableStream({
        start(controller) {
          const sendEvent = (data: unknown) => {
            controller.enqueue(`data: ${JSON.stringify(data)}\n\n`);
          };

          const poller = createTreePoller({
            db,
            prefix: resolvedPrefix,
            sendEvent,
            onError: () => controller.close(),
            rootId: proc.rootId.toString(),
            baseDepth: proc.depth,
            maxDepth: sseOpts.maxDepth,
            visibility: access.visibility,
          });

          poller.start();

          request.signal.addEventListener('abort', () => {
            poller.stop();
            controller.close();
          });
        },
      });

      return new Response(stream, {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      });
    }

    // A task's result file: /api/processes/<id>/result, before ts-rest, which
    // would send the contract's application/octet-stream instead of the
    // file's own Content-Type.
    const resultMatch = pathname.match(/^\/api\/processes\/([^/]+)\/result$/);
    if (resultMatch) {
      const parsed = resultQueryOf(Object.fromEntries(url.searchParams.entries()));
      if (!parsed.ok) {
        return new Response(JSON.stringify(parsed.body), {
          status: parsed.status,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const result = await handlers.getProcessResult(
        ctx, parsed.query, decodeURIComponent(resultMatch[1]), access,
      );
      if (result.status !== 200) {
        return new Response(JSON.stringify(result.body), {
          status: result.status,
          headers: { 'Content-Type': 'application/json', ...('headers' in result ? result.headers : {}) },
        });
      }
      return new Response(Readable.toWeb(result.body) as ReadableStream, {
        status: 200,
        headers: result.headers,
      });
    }

    // Reject legacy metadata.* query params on REST list with explicit migration message.
    if (pathname === '/api/processes') {
      const queryObj = Object.fromEntries(url.searchParams.entries());
      const legacyKeys = detectLegacyMetadataParams(queryObj);
      if (legacyKeys.length > 0) {
        return new Response(
          JSON.stringify({ message: formatLegacyMetadataMessage(legacyKeys) }),
          { status: 400, headers: { 'Content-Type': 'application/json' } },
        );
      }
    }

    // List stream: /api/processes/stream
    if (pathname === '/api/processes/stream') {
      const rawQuery = Object.fromEntries(url.searchParams.entries());
      try {
        checkLegacyMetadataParams(rawQuery);
      } catch (e) {
        if (e instanceof LegacyMetadataParamError) {
          return new Response(
            JSON.stringify({ message: e.message }),
            { status: 400, headers: { 'Content-Type': 'application/json' } },
          );
        }
        throw e;
      }
      let sseOpts;
      try {
        sseOpts = parseSseOptions(rawQuery);
      } catch (e) {
        return new Response(
          JSON.stringify({ message: (e as Error).message }),
          { status: 400, headers: { 'Content-Type': 'application/json' } },
        );
      }
      const { db, prefix: resolvedPrefix } = resolveDb(dbOpts, sseOpts);

      const stream = new ReadableStream({
        start(controller) {
          const sendEvent = (data: unknown) => {
            controller.enqueue(`data: ${JSON.stringify(data)}\n\n`);
          };

          const poller = createListPoller({
            db,
            prefix: resolvedPrefix,
            sendEvent,
            onError: () => controller.close(),
            metadataFilter: sseOpts.metadataFilter,
            scope: access.scope,
            visibility: access.visibility,
          });

          poller.start();

          request.signal.addEventListener('abort', () => {
            poller.stop();
            controller.close();
          });
        },
      });

      return new Response(stream, {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      });
    }

    return requestAccess.run({ access, request }, () => tsRestHandlers(request));
  }

  async function POST(request: Request): Promise<Response> {
    const denied = await authGate(request);
    if (denied) return denied;
    const access = await accessFor(request, hooks);
    return requestAccess.run({ access, request }, () => tsRestHandlers(request));
  }

  return explicit ? { GET, POST } : { GET, POST, ctx };
}
