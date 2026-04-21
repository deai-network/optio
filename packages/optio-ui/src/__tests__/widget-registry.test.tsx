import { describe, it, expect, beforeEach } from 'vitest';
import { registerWidget, getWidget, _clearWidgetRegistry } from '../widgets/registry.js';

describe('widget registry', () => {
  beforeEach(() => {
    _clearWidgetRegistry();
  });

  it('registers and retrieves a widget', () => {
    const Foo = () => null;
    registerWidget('foo', Foo);
    expect(getWidget('foo')).toBe(Foo);
  });

  it('returns undefined for unregistered names', () => {
    expect(getWidget('nope')).toBeUndefined();
  });

  it('replaces on re-registration', () => {
    const A = () => null;
    const B = () => null;
    registerWidget('x', A);
    registerWidget('x', B);
    expect(getWidget('x')).toBe(B);
  });
});
