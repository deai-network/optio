import { type Db } from 'mongodb';
import type { Redis } from 'ioredis';
export interface ListQuery {
    cursor?: string;
    limit: number;
    rootId?: string;
    type?: string;
    state?: string;
    targetId?: string;
}
export declare function listProcesses(db: Db, prefix: string, query: ListQuery): Promise<{
    items: any[];
    nextCursor: string | null;
    totalCount: number;
}>;
export declare function getProcess(db: Db, prefix: string, id: string): Promise<any>;
export declare function getProcessTree(db: Db, prefix: string, id: string, maxDepth?: number): Promise<any>;
export interface PaginationQuery {
    cursor?: string;
    limit: number;
}
export declare function getProcessLog(db: Db, prefix: string, id: string, query: PaginationQuery): Promise<{
    items: any;
    nextCursor: string | null;
    totalCount: any;
} | null>;
export interface TreeLogQuery extends PaginationQuery {
    maxDepth?: number;
}
export declare function getProcessTreeLog(db: Db, prefix: string, id: string, query: TreeLogQuery): Promise<{
    items: any[];
    nextCursor: string | null;
    totalCount: number;
} | null>;
export type CommandResult = {
    status: 200;
    body: any;
} | {
    status: 404;
    body: {
        message: string;
    };
} | {
    status: 409;
    body: {
        message: string;
    };
};
export declare function launchProcess(db: Db, redis: Redis, prefix: string, id: string): Promise<CommandResult>;
export declare function cancelProcess(db: Db, redis: Redis, prefix: string, id: string): Promise<CommandResult>;
export declare function dismissProcess(db: Db, redis: Redis, prefix: string, id: string): Promise<CommandResult>;
export declare function resyncProcesses(redis: Redis, prefix: string, clean?: boolean): Promise<{
    message: string;
}>;
//# sourceMappingURL=handlers.d.ts.map