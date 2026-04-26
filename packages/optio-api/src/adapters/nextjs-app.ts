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
import { resolveDb, type DbOptions } from '../resolve-db.js';

export type OptioApiOptions = {
  redis: Redis;
  prefix?: string;
  authenticate: AuthCallback<Request>;
} & (
  | { db: Db; mongoClient?: never }
  | { mongoClient: MongoClient; db?: never }
);

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function createOptioRouteHandlers(opts: OptioApiOptions) {
  const { redis } = opts;
  const dbOpts: DbOptions = 'mongoClient' in opts && opts.mongoClient ? { mongoClient: opts.mongoClient } : { db: opts.db! };

  const tsRestHandlers = createNextHandler(
    apiContract.processes,
    {
      list: async ({ query }) => {
        const { db, prefix } = resolveDb(dbOpts, query);
        const result = await handlers.listProcesses(db, prefix, query);
        return { status: 200 as const, body: result };
      },
      get: async ({ params, query }) => {
        const { db, prefix } = resolveDb(dbOpts, query);
        const result = await handlers.getProcess(db, prefix, params.id);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getTree: async ({ params, query }) => {
        const { db, prefix } = resolveDb(dbOpts, query);
        const result = await handlers.getProcessTree(db, prefix, params.id, query.maxDepth);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getLog: async ({ params, query }) => {
        const { db, prefix } = resolveDb(dbOpts, query);
        const result = await handlers.getProcessLog(db, prefix, params.id, query);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getTreeLog: async ({ params, query }) => {
        const { db, prefix } = resolveDb(dbOpts, query);
        const result = await handlers.getProcessTreeLog(db, prefix, params.id, query);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      launch: async ({ params, query, body }) => {
        const { db, database, prefix } = resolveDb(dbOpts, query);
        const resume = body?.resume === true;
        const result = await handlers.launchProcess(db, redis, database, prefix, params.id, resume);
        return result as any;
      },
      cancel: async ({ params, query }) => {
        const { db, database, prefix } = resolveDb(dbOpts, query);
        const result = await handlers.cancelProcess(db, redis, database, prefix, params.id);
        return result as any;
      },
      dismiss: async ({ params, query }) => {
        const { db, database, prefix } = resolveDb(dbOpts, query);
        const result = await handlers.dismissProcess(db, redis, database, prefix, params.id);
        return result as any;
      },
      resync: async ({ query, body }: { query: { database?: string; prefix?: string }; body: { clean?: boolean } }) => {
        const { database, prefix } = resolveDb(dbOpts, query);
        const result = await handlers.resyncProcesses(redis, database, prefix, body.clean ?? false);
        return { status: 200 as const, body: result };
      },
    },
    { handlerType: 'app-router' },
  );

  async function GET(request: Request): Promise<Response> {
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
      const database = url.searchParams.get('database') ?? undefined;
      const prefix = url.searchParams.get('prefix') ?? undefined;
      const maxDepth = url.searchParams.get('maxDepth');
      const maxDepthNum = maxDepth !== null ? parseInt(maxDepth, 10) : undefined;

      const { db, prefix: resolvedPrefix } = resolveDb(dbOpts, { database, prefix });

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
            maxDepth: maxDepthNum,
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

    // List stream: /api/processes/stream
    if (pathname === '/api/processes/stream') {
      const database = url.searchParams.get('database') ?? undefined;
      const prefix = url.searchParams.get('prefix') ?? undefined;
      const { db, prefix: resolvedPrefix } = resolveDb(dbOpts, { database, prefix });

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
    return tsRestHandlers(request);
  }

  return { GET, POST };
}
