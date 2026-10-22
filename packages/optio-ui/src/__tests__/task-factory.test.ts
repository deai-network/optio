import { describe, it, expect } from 'vitest';
import i18next from 'i18next';
import type { FieldDefinition } from 'vultus-core';
import { mergeOverrides } from '../task-factory-overrides.js';
import {
  EPHEMERAL_FIELD_ID, launchFormDefinitions,
  fieldDefinitionOf, initialValueOfParameter, isNoValue, refusedParameter, resolveLabel,
  type TaskFactoryParameter,
} from '../task-factory.js';

describe('resolveLabel (spec 1.3: the UI language, else en, else the first)', () => {
  const label = { it: 'Fonti', en: 'Sources', hu: 'Források' };

  it('a string is the label in every language', () => {
    expect(resolveLabel('Sources', 'hu')).toBe('Sources');
  });

  it("the UI language's text", () => {
    expect(resolveLabel(label, 'hu')).toBe('Források');
  });

  it("a regional language falls back to its base language's text", () => {
    expect(resolveLabel(label, 'hu-HU')).toBe('Források');
  });

  it("else the English text", () => {
    expect(resolveLabel(label, 'de')).toBe('Sources');
    expect(resolveLabel(label, undefined)).toBe('Sources');
  });

  it('else the first text', () => {
    expect(resolveLabel({ it: 'Fonti', hu: 'Források' }, 'de')).toBe('Fonti');
  });

  it('no label is none', () => {
    expect(resolveLabel(undefined, 'en')).toBeUndefined();
  });
});

describe('fieldDefinitionOf', () => {
  it('resolves the labels of the field and its choices, and keeps the options vultus knows', () => {
    const p: TaskFactoryParameter = {
      kind: 'manyOf', id: 'sources', label: { en: 'Sources', hu: 'Források' },
      choices: [{ value: 'a', label: { en: 'A', hu: 'Á' } }, { value: 'b' }],
      initialValue: ['a'], required: false,
    };
    expect(fieldDefinitionOf(p, 'hu')).toEqual({
      kind: 'manyOf', id: 'sources', label: 'Források',
      choices: [{ value: 'a', label: 'Á' }, { value: 'b' }],
      initialValue: ['a'],
    });
  });

  it('leaves out required and maxLength, which vultus-core has no option for (the form checks them)', () => {
    const p: TaskFactoryParameter = { kind: 'text', id: 'note', label: 'Note', maxLength: 5, required: true };
    expect(fieldDefinitionOf(p, 'en')).toEqual({ kind: 'text', id: 'note', label: 'Note' });
  });

  it('keeps the numeric options', () => {
    const p: TaskFactoryParameter = {
      kind: 'range', id: 'units', label: 'Units', min: 1, max: 9, step: 1, integer: true,
    };
    expect(fieldDefinitionOf(p, 'en')).toEqual({
      kind: 'range', id: 'units', label: 'Units', min: 1, max: 9, step: 1, integer: true,
    });
  });

  it('a field without a label is named by its id', () => {
    expect(fieldDefinitionOf({ kind: 'boolean', id: 'dry_run' }, 'en').label).toBe('dry_run');
  });
});

describe('initialValueOfParameter', () => {
  it('is the initialValue when there is one', () => {
    expect(initialValueOfParameter({ kind: 'numeric', id: 'n', min: 1, max: 9, initialValue: 4 })).toBe(4);
  });

  it("is the kind's own default otherwise", () => {
    expect(initialValueOfParameter({ kind: 'boolean', id: 'b' })).toBe(false);
    expect(initialValueOfParameter({ kind: 'oneOf', id: 'o', choices: [{ value: 'x' }, { value: 'y' }] })).toBe('x');
    expect(initialValueOfParameter({ kind: 'manyOf', id: 'm', choices: [{ value: 'x' }] })).toEqual([]);
    expect(initialValueOfParameter({ kind: 'numeric', id: 'n', min: 3 })).toBe(3);
    expect(initialValueOfParameter({ kind: 'range', id: 'r', min: 1, max: 9 })).toEqual([1, 9]);
    expect(initialValueOfParameter({ kind: 'text', id: 't' })).toBe('');
  });
});

describe('isNoValue (as optio-core counts a value missing)', () => {
  it('null and undefined are no value for every kind', () => {
    expect(isNoValue({ kind: 'numeric', id: 'n' }, null)).toBe(true);
    expect(isNoValue({ kind: 'boolean', id: 'b' }, undefined)).toBe(true);
  });

  it('the empty text and the empty list are no value', () => {
    expect(isNoValue({ kind: 'text', id: 't' }, '')).toBe(true);
    expect(isNoValue({ kind: 'manyOf', id: 'm', choices: [{ value: 'x' }] }, [])).toBe(true);
  });

  it('false and 0 are values', () => {
    expect(isNoValue({ kind: 'boolean', id: 'b' }, false)).toBe(false);
    expect(isNoValue({ kind: 'numeric', id: 'n' }, 0)).toBe(false);
  });
});

describe('refusedParameter (an invalid-parameters message, "<id>: <what>")', () => {
  it('names the parameter and what is wrong with its value', () => {
    expect(refusedParameter('note: must be at most 5 characters', ['units', 'note']))
      .toEqual({ id: 'note', problem: 'must be at most 5 characters' });
  });

  it('names none when the prefix is no parameter of the form', () => {
    expect(refusedParameter('params: must be an object of parameter values', ['note'])).toBeNull();
  });

  it('names none without a prefix', () => {
    expect(refusedParameter('Pick fewer sources for this many units', ['note'])).toBeNull();
    expect(refusedParameter(undefined, ['note'])).toBeNull();
  });
});

describe('launchFormDefinitions (spec 3.1: the parameters, then the built-in ephemeral field)', () => {
  const i18n = i18next.createInstance();
  i18n.init({ lng: 'en', resources: {} });
  const parameters = [{ kind: 'boolean', id: 'dry_run', label: 'Dry run' }];
  const lastOf = (record: Parameters<typeof launchFormDefinitions>[0]) => {
    const all = launchFormDefinitions(record, 'en', i18n.t) as unknown as Record<string, unknown>[];
    expect(all.map((d) => d.id)).toEqual(['dry_run', EPHEMERAL_FIELD_ID]);
    return all[all.length - 1];
  };

  it('the ephemeral checkbox last, checked without a TTL, with the no-TTL help', () => {
    expect(lastOf({ parameters })).toMatchObject({
      kind: 'boolean',
      id: 'ephemeral',
      label: 'Remove when finished',
      initialValue: true,
      description: 'Checked: the launch disappears from the list when it ends. '
        + 'Unchecked: it stays, with its log, until it is removed.',
    });
  });

  it('unchecked with a TTL, whose help says how long, in the largest unit that divides it', () => {
    const help = (ttlSeconds: number) => lastOf({ parameters, ttlSeconds }).description;
    expect(lastOf({ parameters, ttlSeconds: 3600 }).initialValue).toBe(false);
    expect(help(3600)).toBe(
      'Checked: the launch disappears from the list when it ends. '
      + 'Unchecked: it stays, with its log, until it is removed, at most 1 hour after it ends.',
    );
    expect(help(90)).toContain('at most 90 seconds after');
    expect(help(120)).toContain('at most 2 minutes after');
    expect(help(86400 * 3)).toContain('at most 3 days after');
    expect(help(1)).toContain('at most 1 second after');
  });

  it('a factory without parameters has the checkbox alone', () => {
    expect(launchFormDefinitions({}, 'en', i18n.t).map((d) => d.id)).toEqual(['ephemeral']);
  });
});

describe('mergeOverrides (spec 3.2)', () => {
  const definitions = [
    { kind: 'oneOf', id: 'mode', label: 'Mode', choices: [{ value: 'a' }, { value: 'b' }], nested: { x: 1, y: { z: 2 } } },
    { kind: 'boolean', id: 'dry_run', label: 'Dry run' },
  ] as unknown as FieldDefinition[];
  const merge = (overrides: Record<string, object> | undefined) =>
    mergeOverrides(definitions, overrides as never) as unknown as Record<string, unknown>[];

  it('returns the definitions as they are without overrides', () => {
    expect(mergeOverrides(definitions, undefined)).toEqual(definitions);
  });

  it('merges objects key by key, recursively, and replaces an array', () => {
    const [mode] = merge({ mode: { choices: [{ value: 'c' }], nested: { y: { w: 3 } } } });
    expect(mode.choices).toEqual([{ value: 'c' }]);
    expect(mode.nested).toEqual({ x: 1, y: { z: 2, w: 3 } });
    expect(mode.label).toBe('Mode');
  });

  it('merges plain options into the definition and leaves the others alone', () => {
    const all = merge({ dry_run: { visible: false, initialValue: true } });
    expect(all[1]).toEqual({ kind: 'boolean', id: 'dry_run', label: 'Dry run', visible: false, initialValue: true });
    expect(all[0]).toEqual(definitions[0]);
    expect(definitions[1]).toEqual({ kind: 'boolean', id: 'dry_run', label: 'Dry run' });
  });

  it('a function value replaces', () => {
    const label = () => 'Computed';
    expect(merge({ mode: { label } })[0].label).toBe(label);
  });

  it('throws for an override of a field the form lacks', () => {
    expect(() => merge({ nope: { visible: false } }))
      .toThrow("Task factory overrides: no field 'nope' in the form");
  });

  it('throws for an override of kind or id', () => {
    const message = "Task factory overrides: field 'mode' cannot change its kind or id";
    expect(() => merge({ mode: { kind: 'text' } })).toThrow(message);
    expect(() => merge({ mode: { id: 'other' } })).toThrow(message);
  });
});
