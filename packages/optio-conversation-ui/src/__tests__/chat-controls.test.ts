import { describe, it, expect } from 'vitest';
import { initialChatState, foldControlUpdate, SessionControl } from '../chat';

const CTRLS: SessionControl[] = [
  { id: 'model', kind: 'select', label: 'Model', value: 'a',
    options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }] },
  { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'low',
    levels: ['low', 'high'] },
];

describe('foldControlUpdate', () => {
  it('snapshot replaces controls, keeps items', () => {
    const withItem = { ...initialChatState, items: [{ kind: 'user', text: 'hi', seq: 0 } as any] };
    const s = foldControlUpdate(withItem, { controls: CTRLS });
    expect(s.controls).toHaveLength(2);
    expect(s.items).toHaveLength(1);
  });
  it('value patch updates only the matching control', () => {
    const seeded = { ...initialChatState, controls: CTRLS };
    const s = foldControlUpdate(seeded, { id: 'thinking', value: 'high' });
    expect(s.controls.find((c) => c.id === 'thinking')!.value).toBe('high');
    expect(s.controls.find((c) => c.id === 'model')!.value).toBe('a');
  });
});
