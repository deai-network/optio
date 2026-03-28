// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initServer } from '@ts-rest/fastify';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'optio-contracts';
import type { FastifyInstance } from 'fastify';
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

export function registerProcessRoutes(app: FastifyInstance, opts: OptioApiOptions) {
  const { db, redis } = opts;
  const s = initServer();

  const routes = s.router(apiContract.processes, {
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
  });

  app.register(s.plugin(routes));
}

export function registerProcessStream(app: FastifyInstance, opts: OptioApiOptions) {
  const { db } = opts;

  app.get('/api/processes/:prefix/:id/tree/stream', async (request: any, reply: any) => {
    const { prefix: urlPrefix, id } = request.params as { prefix: string; id: string };
    const { maxDepth } = request.query as { maxDepth?: string };
    const maxDepthNum = maxDepth !== undefined ? parseInt(maxDepth, 10) : undefined;

    const col = db.collection(`${urlPrefix}_processes`);
    const proc = await col.findOne({ _id: new ObjectId(id) });
    if (!proc) {
      reply.code(404).send({ message: 'Process not found' });
      return;
    }

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createTreePoller({
      db,
      prefix: urlPrefix,
      sendEvent,
      onError: () => reply.raw.end(),
      rootId: proc.rootId.toString(),
      baseDepth: proc.depth,
      maxDepth: maxDepthNum,
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });

  app.get('/api/processes/:prefix/stream', async (request: any, reply: any) => {
    const { prefix: urlPrefix } = request.params as { prefix: string };

    reply.raw.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    const sendEvent = (data: unknown) => {
      reply.raw.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    const poller = createListPoller({
      db,
      prefix: urlPrefix,
      sendEvent,
      onError: () => reply.raw.end(),
    });

    poller.start();
    request.raw.on('close', () => poller.stop());
  });
}
