import * as f from 'filtrum-core';
import type { ProcessMetadataPredicate, FilterScalar } from './schemas/process.js';

// Builders are provided by filtrum-core; these wrappers preserve optio's
// ProcessMetadataPredicate return type and the isIn/notIn naming.
export const and = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate =>
  f.and(...(preds as f.Predicate[])) as ProcessMetadataPredicate;
export const or = (...preds: ProcessMetadataPredicate[]): ProcessMetadataPredicate =>
  f.or(...(preds as f.Predicate[])) as ProcessMetadataPredicate;
export const not = (pred: ProcessMetadataPredicate): ProcessMetadataPredicate =>
  f.not(pred as f.Predicate) as ProcessMetadataPredicate;
export const eq = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.eq(field, v) as ProcessMetadataPredicate;
export const ne = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.ne(field, v) as ProcessMetadataPredicate;
export const isIn = (field: string, v: FilterScalar[]): ProcessMetadataPredicate =>
  f.isIn(field, v) as ProcessMetadataPredicate;
export const notIn = (field: string, v: FilterScalar[]): ProcessMetadataPredicate =>
  f.notIn(field, v) as ProcessMetadataPredicate;
export const exists = (field: string, v: boolean = true): ProcessMetadataPredicate =>
  f.exists(field, v) as ProcessMetadataPredicate;
export const gt = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.gt(field, v) as ProcessMetadataPredicate;
export const gte = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.gte(field, v) as ProcessMetadataPredicate;
export const lt = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.lt(field, v) as ProcessMetadataPredicate;
export const lte = (field: string, v: FilterScalar): ProcessMetadataPredicate =>
  f.lte(field, v) as ProcessMetadataPredicate;
