import { type Db } from 'mongodb';
export interface StreamPollerOptions {
    db: Db;
    prefix: string;
    sendEvent: (data: unknown) => void;
    onError: () => void;
}
export interface ListPollerHandle {
    start(): void;
    stop(): void;
}
export declare function createListPoller(opts: StreamPollerOptions): ListPollerHandle;
export interface TreePollerOptions extends StreamPollerOptions {
    rootId: string;
    baseDepth: number;
    maxDepth?: number;
}
export declare function createTreePoller(opts: TreePollerOptions): ListPollerHandle;
//# sourceMappingURL=stream-poller.d.ts.map