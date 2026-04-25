import type { Redis } from 'ioredis';

export function getStreamName(database: string, prefix: string): string {
  return `${database}/${prefix}:commands`;
}

export async function publishLaunch(redis: Redis, database: string, prefix: string, processId: string, resume: boolean = false): Promise<void> {
  await redis.xadd(getStreamName(database, prefix), '*', 'type', 'launch', 'payload', JSON.stringify({ processId, resume }));
}

export async function publishCancel(redis: Redis, database: string, prefix: string, processId: string): Promise<void> {
  await redis.xadd(getStreamName(database, prefix), '*', 'type', 'cancel', 'payload', JSON.stringify({ processId }));
}

export async function publishDismiss(redis: Redis, database: string, prefix: string, processId: string): Promise<void> {
  await redis.xadd(getStreamName(database, prefix), '*', 'type', 'dismiss', 'payload', JSON.stringify({ processId }));
}

export async function publishResync(redis: Redis, database: string, prefix: string, clean: boolean = false): Promise<void> {
  await redis.xadd(getStreamName(database, prefix), '*', 'type', 'resync', 'payload', JSON.stringify({ clean }));
}
