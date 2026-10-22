/**
 * An app's overrides of a task factory's launch form (spec
 * 2026-10-22-task-factory-launch-options §3.2): per field id, a partial
 * vultus-core definition merged into that field's finished definition (the
 * built-in `ephemeral` field included).
 */
import type { FieldDefinition } from 'vultus-core';

/** Field id -> a partial vultus-core definition, merged into that field's definition. */
export type TaskFactoryOverrides = Readonly<Record<string, Partial<FieldDefinition>>>;

const isPlainObject = (v: unknown): v is Record<string, unknown> => (
  typeof v === 'object' && v !== null && !Array.isArray(v)
  && (Object.getPrototypeOf(v) === Object.prototype || Object.getPrototypeOf(v) === null)
);

/** Plain objects merge key by key, recursively; anything else (a value, an array, a function) replaces. */
function mergeValue(base: unknown, override: unknown): unknown {
  if (!isPlainObject(base) || !isPlainObject(override)) return override;
  const merged: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(override)) merged[key] = mergeValue(base[key], value);
  return merged;
}

/**
 * The definitions with the overrides merged in, each into the definition of
 * its id (pure: nothing given is changed). Throws, as an app's mistake, for an
 * override naming a field the form does not have, or changing a field's `kind`
 * or `id`.
 */
export function mergeOverrides(
  definitions: readonly FieldDefinition[],
  overrides: TaskFactoryOverrides | undefined,
): FieldDefinition[] {
  if (!overrides) return [...definitions];
  const ids = new Set(definitions.map((d) => d.id));
  for (const id of Object.keys(overrides)) {
    if (!ids.has(id)) throw new Error(`Task factory overrides: no field '${id}' in the form`);
    if ('kind' in overrides[id] || 'id' in overrides[id]) {
      throw new Error(`Task factory overrides: field '${id}' cannot change its kind or id`);
    }
  }
  return definitions.map((definition) => (
    Object.hasOwn(overrides, definition.id)
      ? mergeValue(definition, overrides[definition.id]) as FieldDefinition
      : definition
  ));
}
