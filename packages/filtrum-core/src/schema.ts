import { z } from 'zod'

export const FilterScalar = z.union([z.string(), z.number(), z.boolean(), z.null()])
export type FilterScalar = z.infer<typeof FilterScalar>

// Generic dotted-path validator: one or more non-empty, non-whitespace segments
// joined by '.'. No backend/namespace assumptions.
export const defaultFieldPath = z
  .string()
  .regex(/^[^.\s]+(\.[^.\s]+)*$/, 'invalid field path')

export const BaseLeafOps = z
  .object({
    eq: FilterScalar.optional(),
    ne: FilterScalar.optional(),
    in: z.array(FilterScalar).optional(),
    nin: z.array(FilterScalar).optional(),
    exists: z.boolean().optional(),
    gt: FilterScalar.optional(),
    gte: FilterScalar.optional(),
    lt: FilterScalar.optional(),
    lte: FilterScalar.optional(),
  })
  .strict()

export interface MakeFilterSchemaOptions {
  extraLeafOps?: Record<string, z.ZodTypeAny>
  allowLegacyFlat?: boolean
  fieldPath?: z.ZodTypeAny
  jsonErrorMessage?: string
}

export function makeFilterSchema(options: MakeFilterSchemaOptions = {}) {
  const {
    extraLeafOps = {},
    allowLegacyFlat = false,
    fieldPath = defaultFieldPath,
    jsonErrorMessage = 'filter must be valid JSON',
  } = options

  const extraShape = Object.fromEntries(
    Object.entries(extraLeafOps).map(([k, v]) => [k, v.optional()]),
  )
  // A leaf must carry at least one operator: an empty `{}` ops object is
  // meaningless in any backend and is rejected (matches optio's prior contract).
  const LeafOps = BaseLeafOps.extend(extraShape).refine(
    (o) => Object.keys(o as Record<string, unknown>).length > 0,
    'leaf needs at least one operator',
  )

  const PredicateSchema: z.ZodType<unknown> = z.lazy(() =>
    z.union([
      z.object({ AND: z.array(PredicateSchema).min(1) }).strict(),
      z.object({ OR: z.array(PredicateSchema).min(1) }).strict(),
      z.object({ NOT: PredicateSchema }).strict(),
      z.record(fieldPath, LeafOps),
    ]),
  )

  const LegacySchema = z.record(fieldPath, FilterScalar)

  const FilterSchema: z.ZodType<unknown> = allowLegacyFlat
    ? z.union([PredicateSchema, LegacySchema])
    : PredicateSchema

  const QueryParamSchema = z
    .string()
    .transform((s, ctx) => {
      try {
        return JSON.parse(s) as unknown
      } catch {
        ctx.addIssue({ code: 'custom', message: jsonErrorMessage })
        return z.NEVER
      }
    })
    .pipe(FilterSchema)
    // An absent query param round-trips to `undefined` (matches optio's prior contract).
    .optional()

  return { LeafOps, PredicateSchema, LegacySchema, FilterSchema, QueryParamSchema }
}
