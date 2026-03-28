// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { createNextHandler } from '@ts-rest/serverless/next';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { Db } from 'mongodb';
import type { Redis } from 'ioredis';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';

export interface OptioApiOptions {
  db: Db;
  redis: Redis;
  prefix?: string;
}

const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });

export function createOptioRouteHandlers(opts: OptioApiOptions) {
  const { db, redis } = opts;

  const tsRestHandlers = createNextHandler(
    apiContract.processes,
    {
      list: async ({ params, query }) => {
        const result = await handlers.listProcesses(db, params.prefix, query);
        return { status: 200 as const, body: result };
      },
      get: async ({ params }) => {
        const result = await handlers.getProcess(db, params.prefix, params.id);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getTree: async ({ params, query }) => {
        const result = await handlers.getProcessTree(db, params.prefix, params.id, query.maxDepth);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getLog: async ({ params, query }) => {
        const result = await handlers.getProcessLog(db, params.prefix, params.id, query);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      getTreeLog: async ({ params, query }) => {
        const result = await handlers.getProcessTreeLog(db, params.prefix, params.id, query);
        if (!result) return { status: 404 as const, body: { message: 'Process not found' } };
        return { status: 200 as const, body: result };
      },
      launch: async ({ params }) => {
        const result = await handlers.launchProcess(db, redis, params.prefix, params.id);
        return result as any;
      },
      cancel: async ({ params }) => {
        const result = await handlers.cancelProcess(db, redis, params.prefix, params.id);
        return result as any;
      },
      dismiss: async ({ params }) => {
        const result = await handlers.dismissProcess(db, redis, params.prefix, params.id);
        return result as any;
      },
      resync: async ({ params, body }: { params: { prefix: string }; body: { clean?: boolean } }) => {
        const result = await handlers.resyncProcesses(redis, params.prefix, body.clean ?? false);
        return { status: 200 as const, body: result };
      },
    },
    { handlerType: 'app-router' },
  );

  async function GET(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const { pathname } = url;

    // Tree stream: /api/processes/<prefix>/<id>/tree/stream
    const treeStreamMatch = pathname.match(/^\/api\/processes\/([^/]+)\/([^/]+)\/tree\/stream$/);
    if (treeStreamMatch) {
      const urlPrefix = treeStreamMatch[1];
      const id = treeStreamMatch[2];
      const maxDepth = url.searchParams.get('maxDepth');
      const maxDepthNum = maxDepth !== null ? parseInt(maxDepth, 10) : undefined;

      const col = db.collection(`${urlPrefix}_processes`);
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
            prefix: urlPrefix,
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

    // List stream: /api/processes/<prefix>/stream
    const listStreamMatch = pathname.match(/^\/api\/processes\/([^/]+)\/stream$/);
    if (listStreamMatch) {
      const urlPrefix = listStreamMatch[1];

      const stream = new ReadableStream({
        start(controller) {
          const sendEvent = (data: unknown) => {
            controller.enqueue(`data: ${JSON.stringify(data)}\n\n`);
          };

          const poller = createListPoller({
            db,
            prefix: urlPrefix,
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
