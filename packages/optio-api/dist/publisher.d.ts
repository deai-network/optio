import type { Redis } from 'ioredis';
export declare function publishLaunch(redis: Redis, prefix: string, processId: string): Promise<void>;
export declare function publishCancel(redis: Redis, prefix: string, processId: string): Promise<void>;
export declare function publishDismiss(redis: Redis, prefix: string, processId: string): Promise<void>;
export declare function publishResync(redis: Redis, prefix: string, clean?: boolean): Promise<void>;
//# sourceMappingURL=publisher.d.ts.map