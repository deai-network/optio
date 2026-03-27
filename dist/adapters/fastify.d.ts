import type { FastifyInstance } from 'fastify';
import type { Db } from 'mongodb';
import type { Redis } from 'ioredis';
export interface FeldwebelApiOptions {
    db: Db;
    redis: Redis;
    prefix: string;
}
export declare function registerProcessRoutes(app: FastifyInstance, opts: FeldwebelApiOptions): void;
export declare function registerProcessStream(app: FastifyInstance, opts: FeldwebelApiOptions): void;
//# sourceMappingURL=fastify.d.ts.map