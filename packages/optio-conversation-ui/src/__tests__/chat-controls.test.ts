import { describe, it, expect } from 'vitest';
import { initialChatState, foldControlUpdate, SessionControl, type CustomControl } from '../chat';

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

const CUSTOM: CustomControl[] = [
  { id: 'speed', label: { en: 'Model', hu: 'Modell' }, description: null, value: 'extensive',
    options: [{ value: 'fast', label: 'Fast', description: null },
              { value: 'extensive', label: 'Extensive', description: null }] },
  { id: 'tone', label: 'Tone', description: null, value: 'concise',
    options: [{ value: 'concise', label: 'Concise', description: null },
              { value: 'detailed', label: 'Detailed', description: null }] },
];

describe('foldControlUpdate: custom controls', () => {
  it('a snapshot carrying customControls replaces them next to the controls', () => {
    const seeded = { ...initialChatState, controls: CTRLS, customControls: CUSTOM };
    const next = [{ ...CUSTOM[0], value: null }];
    const s = foldControlUpdate(seeded, { controls: CTRLS.slice(0, 1), customControls: next });
    expect(s.controls).toHaveLength(1);
    expect(s.customControls).toEqual(next);
  });
  it('a snapshot without customControls keeps them', () => {
    const seeded = { ...initialChatState, controls: CTRLS, customControls: CUSTOM };
    const s = foldControlUpdate(seeded, { controls: CTRLS });
    expect(s.customControls).toBe(CUSTOM);
  });
  it('customControls alone replace them and keep the controls', () => {
    const seeded = { ...initialChatState, controls: CTRLS, customControls: CUSTOM };
    const s = foldControlUpdate(seeded, { customControls: [] });
    expect(s.customControls).toEqual([]);
    expect(s.controls).toBe(CTRLS);
  });
  it('a custom:<id> value patch updates only that custom control (null too)', () => {
    const seeded = { ...initialChatState, controls: CTRLS, customControls: CUSTOM };
    const s = foldControlUpdate(seeded, { id: 'custom:speed', value: 'fast' });
    expect(s.customControls!.map((c) => c.value)).toEqual(['fast', 'concise']);
    expect(s.controls).toBe(CTRLS);
    const cleared = foldControlUpdate(s, { id: 'custom:speed', value: null });
    expect(cleared.customControls![0].value).toBeNull();
  });
});
