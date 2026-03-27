import { z } from 'zod';
export declare const ObjectIdSchema: z.ZodString;
export declare const PaginationQuerySchema: z.ZodObject<{
    cursor: z.ZodOptional<z.ZodString>;
    limit: z.ZodDefault<z.ZodNumber>;
}, "strip", z.ZodTypeAny, {
    limit: number;
    cursor?: string | undefined;
}, {
    cursor?: string | undefined;
    limit?: number | undefined;
}>;
export declare const PaginatedResponseSchema: <T extends z.ZodTypeAny>(itemSchema: T) => z.ZodObject<{
    items: z.ZodArray<T, "many">;
    nextCursor: z.ZodNullable<z.ZodString>;
    totalCount: z.ZodNumber;
}, "strip", z.ZodTypeAny, {
    items: T["_output"][];
    nextCursor: string | null;
    totalCount: number;
}, {
    items: T["_input"][];
    nextCursor: string | null;
    totalCount: number;
}>;
export declare const ErrorSchema: z.ZodObject<{
    message: z.ZodString;
}, "strip", z.ZodTypeAny, {
    message: string;
}, {
    message: string;
}>;
export declare const DateSchema: z.ZodDate;
//# sourceMappingURL=common.d.ts.map