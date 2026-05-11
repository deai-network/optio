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
import { checkAuth } from '../auth.js';
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
        const result = await handlers.listProcesses(ctx, query);
        return { status: 200 as const, body: result };
      },
      get: async ({ params, query }) => {
        const result = await handlers.getProcess(ctx, query, params.id);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getTree: async ({ params, query }) => {
        const result = await handlers.getProcessTree(ctx, query, params.id);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getLog: async ({ params, query }) => {
        const result = await handlers.getProcessLog(ctx, query, params.id);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getTreeLog: async ({ params, query }) => {
        const result = await handlers.getProcessTreeLog(ctx, query, params.id);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      launch: async ({ params, query, body }) => {
        const result = await handlers.launchProcess(ctx, query, params.id, body?.resume === true);
        return result as any;
      },
      cancel: async ({ params, query }) => {
        const result = await handlers.cancelProcess(ctx, query, params.id);
        return result as any;
      },
      dismiss: async ({ params, query }) => {
        const result = await handlers.dismissProcess(ctx, query, params.id);
        return result as any;
      },
      resync: async ({ query, body }: { query: { database?: string; prefix?: string }; body: { clean?: boolean; metadataFilter?: import('../types.js').ProcessMetadataFilter } }) => {
        const result = await handlers.resyncProcesses(ctx, query, body.clean, body.metadataFilter);
        return { status: 202 as const, body: result };
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
    if (pathname === '/api/optio/instances') {
      const instances = await discoverInstances(dbOpts, redis);
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

      const col = db.collection(`${resolvedPrefix}_processes`);
      const proc = await col.findOne({ _id: new ObjectId(id) });
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

    return tsRestHandlers(request);
  }

  async function POST(request: Request): Promise<Response> {
    const denied = await authGate(request);
    if (denied) return denied;
    return tsRestHandlers(request);
  }

  return explicit ? { GET, POST } : { GET, POST, ctx };
}
