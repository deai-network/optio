import type { FilterScalar } from './schema.js'

export type Predicate = Record<string, unknown>

export const and = (...preds: Predicate[]): Predicate => ({ AND: preds })
export const or = (...preds: Predicate[]): Predicate => ({ OR: preds })
export const not = (pred: Predicate): Predicate => ({ NOT: pred })

// Generic leaf for custom ops the core does not know about.
export const leaf = (field: string, op: string, value: unknown): Predicate => ({ [field]: { [op]: value } })

export const eq = (field: string, v: FilterScalar): Predicate => leaf(field, 'eq', v)
export const ne = (field: string, v: FilterScalar): Predicate => leaf(field, 'ne', v)
export const isIn = (field: string, v: FilterScalar[]): Predicate => leaf(field, 'in', v)
export const notIn = (field: string, v: FilterScalar[]): Predicate => leaf(field, 'nin', v)
export const exists = (field: string, v = true): Predicate => leaf(field, 'exists', v)
export const gt = (field: string, v: FilterScalar): Predicate => leaf(field, 'gt', v)
export const gte = (field: string, v: FilterScalar): Predicate => leaf(field, 'gte', v)
export const lt = (field: string, v: FilterScalar): Predicate => leaf(field, 'lt', v)
export const lte = (field: string, v: FilterScalar): Predicate => leaf(field, 'lte', v)
