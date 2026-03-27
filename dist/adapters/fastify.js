// @ts-nocheck — type inference for ts-rest router handlers requires the full
// monorepo type resolution. The adapter is tested via API integration tests.
import { initServer } from '@ts-rest/fastify';
import { initContract } from '@ts-rest/core';
import { processesContract } from 'feldwebel-contracts';
import { ObjectId } from 'mongodb';
import * as handlers from '../handlers.js';
import { createListPoller, createTreePoller } from '../stream-poller.js';
const c = initContract();
const apiContract = c.router({ processes: processesContract }, { pathPrefix: '/api' });
export function registerProcessRoutes(app, opts) {
    const { db, redis, prefix } = opts;
    const s = initServer();
    const routes = s.router(apiContract.processes, {
        list: async ({ params, query }) => {
            const result = await handlers.listProcesses(db, params.prefix, query);
            return { status: 200, body: result };
        },
        get: async ({ params }) => {
            const result = await handlers.getProcess(db, params.prefix, params.id);
            if (!result)
                return { status: 404, body: { message: 'Process not found' } };
            return { status: 200, body: result };
        },
        getTree: async ({ params, query }) => {
            const result = await handlers.getProcessTree(db, params.prefix, params.id, query.maxDepth);
            if (!result)
                return { status: 404, body: { message: 'Process not found' } };
            return { status: 200, body: result };
        },
        getLog: async ({ params, query }) => {
            const result = await handlers.getProcessLog(db, params.prefix, params.id, query);
            if (!result)
                return { status: 404, body: { message: 'Process not found' } };
            return { status: 200, body: result };
        },
        getTreeLog: async ({ params, query }) => {
            const result = await handlers.getProcessTreeLog(db, params.prefix, params.id, query);
            if (!result)
                return { status: 404, body: { message: 'Process not found' } };
            return { status: 200, body: result };
        },
        launch: async ({ params }) => {
            const result = await handlers.launchProcess(db, redis, params.prefix, params.id);
            return result;
        },
        cancel: async ({ params }) => {
            const result = await handlers.cancelProcess(db, redis, params.prefix, params.id);
            return result;
        },
        dismiss: async ({ params }) => {
            const result = await handlers.dismissProcess(db, redis, params.prefix, params.id);
            return result;
        },
        resync: async ({ params, body }) => {
            const result = await handlers.resyncProcesses(redis, params.prefix, body.clean ?? false);
            return { status: 200, body: result };
        },
    });
    app.register(s.plugin(routes));
}
export function registerProcessStream(app, opts) {
    const { db, prefix } = opts;
    app.get('/api/processes/:prefix/:id/tree/stream', async (request, reply) => {
        const { prefix: urlPrefix, id } = request.params;
        const { maxDepth } = request.query;
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
        const sendEvent = (data) => {
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
    app.get('/api/processes/:prefix/stream', async (request, reply) => {
        const { prefix: urlPrefix } = request.params;
        reply.raw.writeHead(200, {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        });
        const sendEvent = (data) => {
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
//# sourceMappingURL=fastify.js.map