import type { Redis } from 'ioredis';

function getStreamName(prefix: string): string {
  return `${prefix}:commands`;
}

export async function publishLaunch(redis: Redis, prefix: string, processId: string): Promise<void> {
  await redis.xadd(getStreamName(prefix), '*', 'type', 'launch', 'payload', JSON.stringify({ processId }));
}

export async function publishCancel(redis: Redis, prefix: string, processId: string): Promise<void> {
  await redis.xadd(getStreamName(prefix), '*', 'type', 'cancel', 'payload', JSON.stringify({ processId }));
}

export async function publishDismiss(redis: Redis, prefix: string, processId: string): Promise<void> {
  await redis.xadd(getStreamName(prefix), '*', 'type', 'dismiss', 'payload', JSON.stringify({ processId }));
}

export async function publishResync(redis: Redis, prefix: string, clean: boolean = false): Promise<void> {
  await redis.xadd(getStreamName(prefix), '*', 'type', 'resync', 'payload', JSON.stringify({ clean }));
}
