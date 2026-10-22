import { describe, it, expect } from 'vitest';
import { logsOfSubtree, namesProcess, subtreeOf } from '../process-subtree.js';

const node = (fields: Record<string, unknown>, children: any[] = []) => ({
  name: String(fields._id), status: { state: 'done' }, progress: { percent: null },
  cancellable: false, order: 0, parentId: null, depth: 0, children, ...fields,
}) as any;

// A factory's tree: two launches, the first with a sub-task.
const STEP = node({ _id: '64b0c0ffee00000000000011', processId: 'step', depth: 2 });
const FIRST = node({ _id: '64b0c0ffee00000000000001', processId: 'fac_0a1b2c3d', depth: 1 }, [STEP]);
const SECOND = node({ _id: '64b0c0ffee00000000000002', processId: 'fac_99887766', depth: 1 });
const FACTORY = node({ _id: '64b0c0ffee00000000000000', processId: 'fac' }, [FIRST, SECOND]);

const entry = (processId: string, message: string) => ({
  timestamp: '2026-04-22T10:00:00Z', level: 'info', message, processId, processLabel: '',
});

describe('namesProcess', () => {
  it('by _id, a 24-hex one in any case (as optio-api resolves it), or by processId', () => {
    expect(namesProcess(FIRST, '64b0c0ffee00000000000001')).toBe(true);
    expect(namesProcess(FIRST, '64B0C0FFEE00000000000001')).toBe(true);
    expect(namesProcess(FIRST, 'fac_0a1b2c3d')).toBe(true);
    expect(namesProcess(FIRST, 'FAC_0A1B2C3D')).toBe(false);
    expect(namesProcess(FIRST, '64b0c0ffee00000000000002')).toBe(false);
  });
});

describe('subtreeOf', () => {
  it('the node the id names, with what is under it, wherever it is in the tree', () => {
    expect(subtreeOf(FACTORY, 'fac_0a1b2c3d')).toBe(FIRST);
    expect(subtreeOf(FACTORY, '64B0C0FFEE00000000000002')).toBe(SECOND);
    expect(subtreeOf(FACTORY, 'step')).toBe(STEP);
    expect(subtreeOf(FACTORY, 'fac')).toBe(FACTORY);
  });

  it('null for an id the tree does not hold', () => {
    expect(subtreeOf(FACTORY, 'other')).toBeNull();
  });
});

describe('logsOfSubtree', () => {
  it("only the entries of the subtree's records", () => {
    const logs = [
      entry(FACTORY._id, 'of the factory'),
      entry(FIRST._id, 'of the first'),
      entry(SECOND._id, 'of the second'),
      entry(STEP._id, 'of the step'),
    ];
    expect(logsOfSubtree(logs, FIRST).map((e) => e.message)).toEqual(['of the first', 'of the step']);
    expect(logsOfSubtree(logs, SECOND).map((e) => e.message)).toEqual(['of the second']);
  });
});
