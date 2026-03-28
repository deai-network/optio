import Fastify from 'fastify';
import fastifyStatic from '@fastify/static';
import { MongoClient } from 'mongodb';
import { Redis } from 'ioredis';
import { registerOptioApi } from 'optio-api/fastify';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export interface DashboardConfig {
  mongodbUrl: string;
  redisUrl: string;
  port: number;
}

export async function startServer(config: DashboardConfig) {
  const app = Fastify({ logger: true });

  // Connect to MongoDB
  const mongoClient = new MongoClient(config.mongodbUrl);
  await mongoClient.connect();
  const dbName = new URL(config.mongodbUrl).pathname.slice(1) || 'optio';
  const db = mongoClient.db(dbName);

  // Connect to Redis
  const redis = new Redis(config.redisUrl);

  // Register Optio API routes and SSE streams
  await registerOptioApi(app, { db, redis });

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
  const shutdown = async () => {
    app.log.info('Shutting down...');
    await app.close();
    redis.disconnect();
    await mongoClient.close();
    process.exit(0);
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  await app.listen({ port: config.port, host: '0.0.0.0' });

  return app;
}
