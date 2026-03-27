import { z } from 'zod';
export declare const processesContract: {
    list: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
        }, {
            prefix: string;
        }>;
        query: z.ZodObject<{
            cursor: z.ZodOptional<z.ZodString>;
            limit: z.ZodDefault<z.ZodNumber>;
        } & {
            rootId: z.ZodOptional<z.ZodString>;
            type: z.ZodOptional<z.ZodString>;
            state: z.ZodOptional<z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>>;
            targetId: z.ZodOptional<z.ZodString>;
        }, "strip", z.ZodTypeAny, {
            limit: number;
            cursor?: string | undefined;
            type?: string | undefined;
            state?: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled" | undefined;
            rootId?: string | undefined;
            targetId?: string | undefined;
        }, {
            cursor?: string | undefined;
            limit?: number | undefined;
            type?: string | undefined;
            state?: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled" | undefined;
            rootId?: string | undefined;
            targetId?: string | undefined;
        }>;
        summary: "List and filter processes";
        method: "GET";
        path: "/processes/:prefix";
        responses: {
            200: z.ZodObject<{
                items: z.ZodArray<z.ZodObject<{
                    _id: z.ZodString;
                    processId: z.ZodString;
                    name: z.ZodString;
                    params: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                    metadata: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                    parentId: z.ZodOptional<z.ZodString>;
                    rootId: z.ZodString;
                    depth: z.ZodNumber;
                    order: z.ZodNumber;
                    cancellable: z.ZodBoolean;
                    special: z.ZodOptional<z.ZodBoolean>;
                    warning: z.ZodOptional<z.ZodString>;
                    status: z.ZodObject<{
                        state: z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>;
                        error: z.ZodOptional<z.ZodString>;
                        runningSince: z.ZodOptional<z.ZodDate>;
                        doneAt: z.ZodOptional<z.ZodDate>;
                        duration: z.ZodOptional<z.ZodNumber>;
                        failedAt: z.ZodOptional<z.ZodDate>;
                        stoppedAt: z.ZodOptional<z.ZodDate>;
                    }, "strip", z.ZodTypeAny, {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    }, {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    }>;
                    progress: z.ZodObject<{
                        percent: z.ZodNullable<z.ZodNumber>;
                        message: z.ZodOptional<z.ZodString>;
                    }, "strip", z.ZodTypeAny, {
                        percent: number | null;
                        message?: string | undefined;
                    }, {
                        percent: number | null;
                        message?: string | undefined;
                    }>;
                    log: z.ZodArray<z.ZodObject<{
                        timestamp: z.ZodDate;
                        level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                        message: z.ZodString;
                        data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                    }, "strip", z.ZodTypeAny, {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }, {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }>, "many">;
                    createdAt: z.ZodDate;
                }, "strip", z.ZodTypeAny, {
                    status: {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    };
                    _id: string;
                    processId: string;
                    name: string;
                    rootId: string;
                    depth: number;
                    order: number;
                    cancellable: boolean;
                    progress: {
                        percent: number | null;
                        message?: string | undefined;
                    };
                    log: {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }[];
                    createdAt: Date;
                    params?: Record<string, unknown> | undefined;
                    warning?: string | undefined;
                    metadata?: Record<string, unknown> | undefined;
                    parentId?: string | undefined;
                    special?: boolean | undefined;
                }, {
                    status: {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    };
                    _id: string;
                    processId: string;
                    name: string;
                    rootId: string;
                    depth: number;
                    order: number;
                    cancellable: boolean;
                    progress: {
                        percent: number | null;
                        message?: string | undefined;
                    };
                    log: {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }[];
                    createdAt: Date;
                    params?: Record<string, unknown> | undefined;
                    warning?: string | undefined;
                    metadata?: Record<string, unknown> | undefined;
                    parentId?: string | undefined;
                    special?: boolean | undefined;
                }>, "many">;
                nextCursor: z.ZodNullable<z.ZodString>;
                totalCount: z.ZodNumber;
            }, "strip", z.ZodTypeAny, {
                items: {
                    status: {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    };
                    _id: string;
                    processId: string;
                    name: string;
                    rootId: string;
                    depth: number;
                    order: number;
                    cancellable: boolean;
                    progress: {
                        percent: number | null;
                        message?: string | undefined;
                    };
                    log: {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }[];
                    createdAt: Date;
                    params?: Record<string, unknown> | undefined;
                    warning?: string | undefined;
                    metadata?: Record<string, unknown> | undefined;
                    parentId?: string | undefined;
                    special?: boolean | undefined;
                }[];
                nextCursor: string | null;
                totalCount: number;
            }, {
                items: {
                    status: {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    };
                    _id: string;
                    processId: string;
                    name: string;
                    rootId: string;
                    depth: number;
                    order: number;
                    cancellable: boolean;
                    progress: {
                        percent: number | null;
                        message?: string | undefined;
                    };
                    log: {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }[];
                    createdAt: Date;
                    params?: Record<string, unknown> | undefined;
                    warning?: string | undefined;
                    metadata?: Record<string, unknown> | undefined;
                    parentId?: string | undefined;
                    special?: boolean | undefined;
                }[];
                nextCursor: string | null;
                totalCount: number;
            }>;
        };
    };
    get: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
            id: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
            id: string;
        }, {
            prefix: string;
            id: string;
        }>;
        summary: "Get single process";
        method: "GET";
        path: "/processes/:prefix/:id";
        responses: {
            200: z.ZodObject<{
                _id: z.ZodString;
                processId: z.ZodString;
                name: z.ZodString;
                params: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                metadata: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                parentId: z.ZodOptional<z.ZodString>;
                rootId: z.ZodString;
                depth: z.ZodNumber;
                order: z.ZodNumber;
                cancellable: z.ZodBoolean;
                special: z.ZodOptional<z.ZodBoolean>;
                warning: z.ZodOptional<z.ZodString>;
                status: z.ZodObject<{
                    state: z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>;
                    error: z.ZodOptional<z.ZodString>;
                    runningSince: z.ZodOptional<z.ZodDate>;
                    doneAt: z.ZodOptional<z.ZodDate>;
                    duration: z.ZodOptional<z.ZodNumber>;
                    failedAt: z.ZodOptional<z.ZodDate>;
                    stoppedAt: z.ZodOptional<z.ZodDate>;
                }, "strip", z.ZodTypeAny, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }>;
                progress: z.ZodObject<{
                    percent: z.ZodNullable<z.ZodNumber>;
                    message: z.ZodOptional<z.ZodString>;
                }, "strip", z.ZodTypeAny, {
                    percent: number | null;
                    message?: string | undefined;
                }, {
                    percent: number | null;
                    message?: string | undefined;
                }>;
                log: z.ZodArray<z.ZodObject<{
                    timestamp: z.ZodDate;
                    level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                    message: z.ZodString;
                    data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                }, "strip", z.ZodTypeAny, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }>, "many">;
                createdAt: z.ZodDate;
            }, "strip", z.ZodTypeAny, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }>;
            404: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
        };
    };
    getTree: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
            id: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
            id: string;
        }, {
            prefix: string;
            id: string;
        }>;
        query: z.ZodObject<{
            maxDepth: z.ZodOptional<z.ZodNumber>;
        }, "strip", z.ZodTypeAny, {
            maxDepth?: number | undefined;
        }, {
            maxDepth?: number | undefined;
        }>;
        summary: "Get full process subtree";
        method: "GET";
        path: "/processes/:prefix/:id/tree";
        responses: {
            200: z.ZodObject<{
                _id: z.ZodString;
                processId: z.ZodString;
                name: z.ZodString;
                params: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                metadata: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                parentId: z.ZodOptional<z.ZodString>;
                rootId: z.ZodString;
                depth: z.ZodNumber;
                order: z.ZodNumber;
                cancellable: z.ZodBoolean;
                special: z.ZodOptional<z.ZodBoolean>;
                warning: z.ZodOptional<z.ZodString>;
                status: z.ZodObject<{
                    state: z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>;
                    error: z.ZodOptional<z.ZodString>;
                    runningSince: z.ZodOptional<z.ZodDate>;
                    doneAt: z.ZodOptional<z.ZodDate>;
                    duration: z.ZodOptional<z.ZodNumber>;
                    failedAt: z.ZodOptional<z.ZodDate>;
                    stoppedAt: z.ZodOptional<z.ZodDate>;
                }, "strip", z.ZodTypeAny, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }>;
                progress: z.ZodObject<{
                    percent: z.ZodNullable<z.ZodNumber>;
                    message: z.ZodOptional<z.ZodString>;
                }, "strip", z.ZodTypeAny, {
                    percent: number | null;
                    message?: string | undefined;
                }, {
                    percent: number | null;
                    message?: string | undefined;
                }>;
                log: z.ZodArray<z.ZodObject<{
                    timestamp: z.ZodDate;
                    level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                    message: z.ZodString;
                    data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                }, "strip", z.ZodTypeAny, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }>, "many">;
                createdAt: z.ZodDate;
            } & {
                children: z.ZodArray<z.ZodLazy<z.ZodObject<{
                    _id: z.ZodString;
                    processId: z.ZodString;
                    name: z.ZodString;
                    params: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                    metadata: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                    parentId: z.ZodOptional<z.ZodString>;
                    rootId: z.ZodString;
                    depth: z.ZodNumber;
                    order: z.ZodNumber;
                    cancellable: z.ZodBoolean;
                    special: z.ZodOptional<z.ZodBoolean>;
                    warning: z.ZodOptional<z.ZodString>;
                    status: z.ZodObject<{
                        state: z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>;
                        error: z.ZodOptional<z.ZodString>;
                        runningSince: z.ZodOptional<z.ZodDate>;
                        doneAt: z.ZodOptional<z.ZodDate>;
                        duration: z.ZodOptional<z.ZodNumber>;
                        failedAt: z.ZodOptional<z.ZodDate>;
                        stoppedAt: z.ZodOptional<z.ZodDate>;
                    }, "strip", z.ZodTypeAny, {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    }, {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    }>;
                    progress: z.ZodObject<{
                        percent: z.ZodNullable<z.ZodNumber>;
                        message: z.ZodOptional<z.ZodString>;
                    }, "strip", z.ZodTypeAny, {
                        percent: number | null;
                        message?: string | undefined;
                    }, {
                        percent: number | null;
                        message?: string | undefined;
                    }>;
                    log: z.ZodArray<z.ZodObject<{
                        timestamp: z.ZodDate;
                        level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                        message: z.ZodString;
                        data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                    }, "strip", z.ZodTypeAny, {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }, {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }>, "many">;
                    createdAt: z.ZodDate;
                } & {
                    children: z.ZodArray<z.ZodAny, "many">;
                }, "strip", z.ZodTypeAny, {
                    status: {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    };
                    _id: string;
                    processId: string;
                    name: string;
                    rootId: string;
                    depth: number;
                    order: number;
                    cancellable: boolean;
                    progress: {
                        percent: number | null;
                        message?: string | undefined;
                    };
                    log: {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }[];
                    createdAt: Date;
                    children: any[];
                    params?: Record<string, unknown> | undefined;
                    warning?: string | undefined;
                    metadata?: Record<string, unknown> | undefined;
                    parentId?: string | undefined;
                    special?: boolean | undefined;
                }, {
                    status: {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    };
                    _id: string;
                    processId: string;
                    name: string;
                    rootId: string;
                    depth: number;
                    order: number;
                    cancellable: boolean;
                    progress: {
                        percent: number | null;
                        message?: string | undefined;
                    };
                    log: {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }[];
                    createdAt: Date;
                    children: any[];
                    params?: Record<string, unknown> | undefined;
                    warning?: string | undefined;
                    metadata?: Record<string, unknown> | undefined;
                    parentId?: string | undefined;
                    special?: boolean | undefined;
                }>>, "many">;
            }, "strip", z.ZodTypeAny, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                children: {
                    status: {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    };
                    _id: string;
                    processId: string;
                    name: string;
                    rootId: string;
                    depth: number;
                    order: number;
                    cancellable: boolean;
                    progress: {
                        percent: number | null;
                        message?: string | undefined;
                    };
                    log: {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }[];
                    createdAt: Date;
                    children: any[];
                    params?: Record<string, unknown> | undefined;
                    warning?: string | undefined;
                    metadata?: Record<string, unknown> | undefined;
                    parentId?: string | undefined;
                    special?: boolean | undefined;
                }[];
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                children: {
                    status: {
                        state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                        error?: string | undefined;
                        runningSince?: Date | undefined;
                        doneAt?: Date | undefined;
                        duration?: number | undefined;
                        failedAt?: Date | undefined;
                        stoppedAt?: Date | undefined;
                    };
                    _id: string;
                    processId: string;
                    name: string;
                    rootId: string;
                    depth: number;
                    order: number;
                    cancellable: boolean;
                    progress: {
                        percent: number | null;
                        message?: string | undefined;
                    };
                    log: {
                        message: string;
                        timestamp: Date;
                        level: "error" | "event" | "info" | "debug" | "warning";
                        data?: Record<string, unknown> | undefined;
                    }[];
                    createdAt: Date;
                    children: any[];
                    params?: Record<string, unknown> | undefined;
                    warning?: string | undefined;
                    metadata?: Record<string, unknown> | undefined;
                    parentId?: string | undefined;
                    special?: boolean | undefined;
                }[];
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }>;
            404: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
        };
    };
    getLog: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
            id: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
            id: string;
        }, {
            prefix: string;
            id: string;
        }>;
        query: z.ZodObject<{
            cursor: z.ZodOptional<z.ZodString>;
            limit: z.ZodDefault<z.ZodNumber>;
        }, "strip", z.ZodTypeAny, {
            limit: number;
            cursor?: string | undefined;
        }, {
            cursor?: string | undefined;
            limit?: number | undefined;
        }>;
        summary: "Get log entries for a single process";
        method: "GET";
        path: "/processes/:prefix/:id/log";
        responses: {
            200: z.ZodObject<{
                items: z.ZodArray<z.ZodObject<{
                    timestamp: z.ZodDate;
                    level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                    message: z.ZodString;
                    data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                }, "strip", z.ZodTypeAny, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }>, "many">;
                nextCursor: z.ZodNullable<z.ZodString>;
                totalCount: z.ZodNumber;
            }, "strip", z.ZodTypeAny, {
                items: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                nextCursor: string | null;
                totalCount: number;
            }, {
                items: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                nextCursor: string | null;
                totalCount: number;
            }>;
            404: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
        };
    };
    getTreeLog: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
            id: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
            id: string;
        }, {
            prefix: string;
            id: string;
        }>;
        query: z.ZodObject<{
            cursor: z.ZodOptional<z.ZodString>;
            limit: z.ZodDefault<z.ZodNumber>;
        } & {
            maxDepth: z.ZodOptional<z.ZodNumber>;
        }, "strip", z.ZodTypeAny, {
            limit: number;
            cursor?: string | undefined;
            maxDepth?: number | undefined;
        }, {
            cursor?: string | undefined;
            limit?: number | undefined;
            maxDepth?: number | undefined;
        }>;
        summary: "Get merged log entries across subtree";
        method: "GET";
        path: "/processes/:prefix/:id/tree/log";
        responses: {
            200: z.ZodObject<{
                items: z.ZodArray<z.ZodObject<{
                    timestamp: z.ZodDate;
                    level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                    message: z.ZodString;
                    data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                } & {
                    processId: z.ZodString;
                    processLabel: z.ZodString;
                }, "strip", z.ZodTypeAny, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    processId: string;
                    processLabel: string;
                    data?: Record<string, unknown> | undefined;
                }, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    processId: string;
                    processLabel: string;
                    data?: Record<string, unknown> | undefined;
                }>, "many">;
                nextCursor: z.ZodNullable<z.ZodString>;
                totalCount: z.ZodNumber;
            }, "strip", z.ZodTypeAny, {
                items: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    processId: string;
                    processLabel: string;
                    data?: Record<string, unknown> | undefined;
                }[];
                nextCursor: string | null;
                totalCount: number;
            }, {
                items: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    processId: string;
                    processLabel: string;
                    data?: Record<string, unknown> | undefined;
                }[];
                nextCursor: string | null;
                totalCount: number;
            }>;
            404: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
        };
    };
    launch: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
            id: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
            id: string;
        }, {
            prefix: string;
            id: string;
        }>;
        summary: "Launch a process";
        method: "POST";
        body: typeof import("@ts-rest/core").ContractNoBody;
        path: "/processes/:prefix/:id/launch";
        responses: {
            200: z.ZodObject<{
                _id: z.ZodString;
                processId: z.ZodString;
                name: z.ZodString;
                params: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                metadata: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                parentId: z.ZodOptional<z.ZodString>;
                rootId: z.ZodString;
                depth: z.ZodNumber;
                order: z.ZodNumber;
                cancellable: z.ZodBoolean;
                special: z.ZodOptional<z.ZodBoolean>;
                warning: z.ZodOptional<z.ZodString>;
                status: z.ZodObject<{
                    state: z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>;
                    error: z.ZodOptional<z.ZodString>;
                    runningSince: z.ZodOptional<z.ZodDate>;
                    doneAt: z.ZodOptional<z.ZodDate>;
                    duration: z.ZodOptional<z.ZodNumber>;
                    failedAt: z.ZodOptional<z.ZodDate>;
                    stoppedAt: z.ZodOptional<z.ZodDate>;
                }, "strip", z.ZodTypeAny, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }>;
                progress: z.ZodObject<{
                    percent: z.ZodNullable<z.ZodNumber>;
                    message: z.ZodOptional<z.ZodString>;
                }, "strip", z.ZodTypeAny, {
                    percent: number | null;
                    message?: string | undefined;
                }, {
                    percent: number | null;
                    message?: string | undefined;
                }>;
                log: z.ZodArray<z.ZodObject<{
                    timestamp: z.ZodDate;
                    level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                    message: z.ZodString;
                    data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                }, "strip", z.ZodTypeAny, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }>, "many">;
                createdAt: z.ZodDate;
            }, "strip", z.ZodTypeAny, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }>;
            404: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
            409: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
        };
    };
    cancel: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
            id: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
            id: string;
        }, {
            prefix: string;
            id: string;
        }>;
        summary: "Request process cancellation";
        method: "POST";
        body: typeof import("@ts-rest/core").ContractNoBody;
        path: "/processes/:prefix/:id/cancel";
        responses: {
            200: z.ZodObject<{
                _id: z.ZodString;
                processId: z.ZodString;
                name: z.ZodString;
                params: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                metadata: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                parentId: z.ZodOptional<z.ZodString>;
                rootId: z.ZodString;
                depth: z.ZodNumber;
                order: z.ZodNumber;
                cancellable: z.ZodBoolean;
                special: z.ZodOptional<z.ZodBoolean>;
                warning: z.ZodOptional<z.ZodString>;
                status: z.ZodObject<{
                    state: z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>;
                    error: z.ZodOptional<z.ZodString>;
                    runningSince: z.ZodOptional<z.ZodDate>;
                    doneAt: z.ZodOptional<z.ZodDate>;
                    duration: z.ZodOptional<z.ZodNumber>;
                    failedAt: z.ZodOptional<z.ZodDate>;
                    stoppedAt: z.ZodOptional<z.ZodDate>;
                }, "strip", z.ZodTypeAny, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }>;
                progress: z.ZodObject<{
                    percent: z.ZodNullable<z.ZodNumber>;
                    message: z.ZodOptional<z.ZodString>;
                }, "strip", z.ZodTypeAny, {
                    percent: number | null;
                    message?: string | undefined;
                }, {
                    percent: number | null;
                    message?: string | undefined;
                }>;
                log: z.ZodArray<z.ZodObject<{
                    timestamp: z.ZodDate;
                    level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                    message: z.ZodString;
                    data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                }, "strip", z.ZodTypeAny, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }>, "many">;
                createdAt: z.ZodDate;
            }, "strip", z.ZodTypeAny, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }>;
            404: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
            409: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
        };
    };
    dismiss: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
            id: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
            id: string;
        }, {
            prefix: string;
            id: string;
        }>;
        summary: "Dismiss process (reset to idle)";
        method: "POST";
        body: typeof import("@ts-rest/core").ContractNoBody;
        path: "/processes/:prefix/:id/dismiss";
        responses: {
            200: z.ZodObject<{
                _id: z.ZodString;
                processId: z.ZodString;
                name: z.ZodString;
                params: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                metadata: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                parentId: z.ZodOptional<z.ZodString>;
                rootId: z.ZodString;
                depth: z.ZodNumber;
                order: z.ZodNumber;
                cancellable: z.ZodBoolean;
                special: z.ZodOptional<z.ZodBoolean>;
                warning: z.ZodOptional<z.ZodString>;
                status: z.ZodObject<{
                    state: z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>;
                    error: z.ZodOptional<z.ZodString>;
                    runningSince: z.ZodOptional<z.ZodDate>;
                    doneAt: z.ZodOptional<z.ZodDate>;
                    duration: z.ZodOptional<z.ZodNumber>;
                    failedAt: z.ZodOptional<z.ZodDate>;
                    stoppedAt: z.ZodOptional<z.ZodDate>;
                }, "strip", z.ZodTypeAny, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }, {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                }>;
                progress: z.ZodObject<{
                    percent: z.ZodNullable<z.ZodNumber>;
                    message: z.ZodOptional<z.ZodString>;
                }, "strip", z.ZodTypeAny, {
                    percent: number | null;
                    message?: string | undefined;
                }, {
                    percent: number | null;
                    message?: string | undefined;
                }>;
                log: z.ZodArray<z.ZodObject<{
                    timestamp: z.ZodDate;
                    level: z.ZodEnum<["event", "info", "debug", "warning", "error"]>;
                    message: z.ZodString;
                    data: z.ZodOptional<z.ZodRecord<z.ZodString, z.ZodUnknown>>;
                }, "strip", z.ZodTypeAny, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }, {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }>, "many">;
                createdAt: z.ZodDate;
            }, "strip", z.ZodTypeAny, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }, {
                status: {
                    state: "idle" | "scheduled" | "running" | "done" | "failed" | "cancel_requested" | "cancelling" | "cancelled";
                    error?: string | undefined;
                    runningSince?: Date | undefined;
                    doneAt?: Date | undefined;
                    duration?: number | undefined;
                    failedAt?: Date | undefined;
                    stoppedAt?: Date | undefined;
                };
                _id: string;
                processId: string;
                name: string;
                rootId: string;
                depth: number;
                order: number;
                cancellable: boolean;
                progress: {
                    percent: number | null;
                    message?: string | undefined;
                };
                log: {
                    message: string;
                    timestamp: Date;
                    level: "error" | "event" | "info" | "debug" | "warning";
                    data?: Record<string, unknown> | undefined;
                }[];
                createdAt: Date;
                params?: Record<string, unknown> | undefined;
                warning?: string | undefined;
                metadata?: Record<string, unknown> | undefined;
                parentId?: string | undefined;
                special?: boolean | undefined;
            }>;
            404: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
            409: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
        };
    };
    resync: {
        pathParams: z.ZodObject<{
            prefix: z.ZodString;
        }, "strip", z.ZodTypeAny, {
            prefix: string;
        }, {
            prefix: string;
        }>;
        summary: "Re-sync process definitions";
        method: "POST";
        body: z.ZodObject<{
            clean: z.ZodOptional<z.ZodBoolean>;
        }, "strip", z.ZodTypeAny, {
            clean?: boolean | undefined;
        }, {
            clean?: boolean | undefined;
        }>;
        path: "/processes/:prefix/resync";
        responses: {
            200: z.ZodObject<{
                message: z.ZodString;
            }, "strip", z.ZodTypeAny, {
                message: string;
            }, {
                message: string;
            }>;
        };
    };
};
//# sourceMappingURL=contract.d.ts.map