import { z } from 'zod';
export declare const ProcessStateSchema: z.ZodEnum<["idle", "scheduled", "running", "done", "failed", "cancel_requested", "cancelling", "cancelled"]>;
export declare const LogEntrySchema: z.ZodObject<{
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
}>;
export declare const ProcessSchema: z.ZodObject<{
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
export type Process = z.infer<typeof ProcessSchema>;
export type ProcessState = z.infer<typeof ProcessStateSchema>;
export type LogEntry = z.infer<typeof LogEntrySchema>;
//# sourceMappingURL=process.d.ts.map