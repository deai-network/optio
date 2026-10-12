import { describe, it, expect, beforeEach } from 'vitest';
import { registerWidget, getWidget, getSuspendedWidget, _clearWidgetRegistry } from '../widgets/registry.js';

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

  describe('suspended variant', () => {
    it('registers and retrieves a suspended variant via options.suspended', () => {
      const Live = () => null;
      const Suspended = () => null;
      registerWidget('foo', Live, { suspended: Suspended });
      expect(getWidget('foo')).toBe(Live);
      expect(getSuspendedWidget('foo')).toBe(Suspended);
    });

    it('returns undefined for a name with no suspended variant registered', () => {
      registerWidget('foo', () => null);
      expect(getSuspendedWidget('foo')).toBeUndefined();
    });

    it('existing two-arg call sites keep working unchanged (no options param)', () => {
      const Live = () => null;
      registerWidget('foo', Live);
      expect(getWidget('foo')).toBe(Live);
      expect(getSuspendedWidget('foo')).toBeUndefined();
    });

    it('re-registering the live component without options leaves a previously-registered suspended variant untouched', () => {
      const LiveA = () => null;
      const LiveB = () => null;
      const Suspended = () => null;
      registerWidget('foo', LiveA, { suspended: Suspended });
      registerWidget('foo', LiveB);
      expect(getWidget('foo')).toBe(LiveB);
      expect(getSuspendedWidget('foo')).toBe(Suspended);
    });

    it('replaces the suspended variant on re-registration with a new one', () => {
      const SuspendedA = () => null;
      const SuspendedB = () => null;
      registerWidget('foo', () => null, { suspended: SuspendedA });
      registerWidget('foo', () => null, { suspended: SuspendedB });
      expect(getSuspendedWidget('foo')).toBe(SuspendedB);
    });

    it('_clearWidgetRegistry clears both maps', () => {
      registerWidget('foo', () => null, { suspended: () => null });
      _clearWidgetRegistry();
      expect(getWidget('foo')).toBeUndefined();
      expect(getSuspendedWidget('foo')).toBeUndefined();
    });
  });
});
