import type {
  ProcessMetadataPredicate,
  FilterScalar,
} from './schemas/process.js';

// Combinators.
export const and = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate =>
  ({ AND: preds } as ProcessMetadataPredicate);

export const or = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate =>
  ({ OR: preds } as ProcessMetadataPredicate);

export const not = (pred: ProcessMetadataPredicate): ProcessMetadataPredicate =>
  ({ NOT: pred } as ProcessMetadataPredicate);

// Leaf builders. `field` is a dotted path under `metadata.*` (auto-prefixed
// at translation time). `isIn` / `notIn` avoid the `in` JS reserved-word
// collision; `not` is the combinator and does not double as a leaf negation
// (use `not(eq(...))`).
export const eq = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { eq: v } } as ProcessMetadataPredicate);

export const ne = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { ne: v } } as ProcessMetadataPredicate);

export const isIn = (field: string, v: FilterScalar[]): ProcessMetadataPredicate =>
  ({ [field]: { in: v } } as ProcessMetadataPredicate);

export const notIn = (field: string, v: FilterScalar[]): ProcessMetadataPredicate =>
  ({ [field]: { nin: v } } as ProcessMetadataPredicate);

export const exists = (field: string, v: boolean = true): ProcessMetadataPredicate =>
  ({ [field]: { exists: v } } as ProcessMetadataPredicate);

export const gt = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { gt: v } } as ProcessMetadataPredicate);

export const gte = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { gte: v } } as ProcessMetadataPredicate);

export const lt = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { lt: v } } as ProcessMetadataPredicate);

export const lte = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  ({ [field]: { lte: v } } as ProcessMetadataPredicate);
