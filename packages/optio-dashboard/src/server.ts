import Fastify from 'fastify';
import fastifyStatic from '@fastify/static';
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { registerOptioApi } from 'optio-api/fastify';
import { fromNodeHeaders } from 'better-auth/node';
import { createAuth, upsertAdminUser } from './auth-server.js';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export interface DashboardConfig {
  mongodbUrl: string;
  redisUrl: string;
  port: number;
  password: string;
}

export async function startServer(config: DashboardConfig) {
  const app = Fastify({
    logger: {
      transport: {
        target: 'pino-pretty',
        options: { translateTime: 'HH:MM:ss', ignore: 'pid,hostname' },
      },
    },
  });

  // Connect to MongoDB
  app.log.info(`Connecting to MongoDB: ${config.mongodbUrl}`);
  const mongoClient = new MongoClient(config.mongodbUrl);
  await mongoClient.connect();
  const dbName = new URL(config.mongodbUrl).pathname.slice(1) || 'optio';
  const db = mongoClient.db(dbName);

  // Connect to Redis
  app.log.info(`Connecting to Redis: ${config.redisUrl}`);
  const redis = new Redis(config.redisUrl);

  // Set up Better Auth (use the password as the signing secret so
  // changing the password automatically invalidates all sessions)
  const baseURL = process.env.BETTER_AUTH_URL ?? `http://localhost:${config.port}`;
  const auth = createAuth(db, config.password, baseURL);
  await upsertAdminUser(db, auth, config.password);

  // Mount Better Auth routes at /api/auth/*
  app.route({
    method: ['GET', 'POST'],
    url: '/api/auth/*',
    async handler(request, reply) {
      const url = new URL(request.url, `http://${request.headers.host}`);
      const headers = fromNodeHeaders(request.headers);
      const req = new Request(url.toString(), {
        method: request.method,
        headers,
        ...(request.body ? { body: JSON.stringify(request.body) } : {}),
      });
      const response = await auth.handler(req);
      reply.status(response.status);
      response.headers.forEach((value, key) => reply.header(key, value));
      const body = await response.text();
      reply.send(body || null);
    },
  });

  // Register Optio API routes with session-based authenticate
  await registerOptioApi(app, {
    mongoClient,
    redis,
    authenticate: async (request: import('fastify').FastifyRequest) => {
      const url = request.url;
      // Only enforce auth on Optio API routes.
      // Static files (served by fastify-static) and Better Auth routes
      // (/api/auth/*) must pass through — the client-side auth gate and
      // Better Auth itself handle those respectively.
      if (!url.startsWith('/api/') || url.startsWith('/api/auth/')) {
        return 'operator';
      }
      const session = await auth.api.getSession({
        headers: fromNodeHeaders(request.headers),
      });
      return session ? 'operator' : null;
    },
  });

  // Serve the pre-built React app
  await app.register(fastifyStatic, {
    root: path.join(__dirname, 'public'),
    wildcard: false,
  });

  // SPA fallback: serve index.html for all non-API, non-file routes
  app.setNotFoundHandler(async (request, reply) => {
    if (request.url.startsWith('/api/')) {
      reply.code(404).send({ message: 'Not found' });
      return;
    }
    return reply.sendFile('index.html');
  });

  // Graceful shutdown
  let shuttingDown = false;
  const shutdown = async () => {
    if (shuttingDown) {
      process.exit(1);
    }
    shuttingDown = true;
    app.log.info('Shutting down...');
    // Force exit after 3 seconds if app.close() hangs (e.g., open SSE connections)
    const forceTimer = setTimeout(() => process.exit(0), 3000);
    forceTimer.unref();
    try {
      await app.close();
      redis.disconnect();
      await mongoClient.close();
    } catch {
      // Ignore close errors during shutdown
    }
    process.exit(0);
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  await app.listen({ port: config.port, host: '0.0.0.0' });

  return app;
}
