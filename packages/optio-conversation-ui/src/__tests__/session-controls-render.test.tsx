import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConversationView } from '../ConversationView';
import { initialChatState, SessionControl } from '../chat';

const controls: SessionControl[] = [
  { id: 'model', kind: 'select', label: 'Model', value: 'a',
    options: [{ value: 'a', label: 'A' },
              { value: 'b', label: 'B', disabled: true, whyDisabled: 'plan-gated' }] },
  { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'low', levels: ['low', 'high'] },
  { id: 'wide', kind: 'boolean', label: 'Wide', value: false },
];

function base(onControlChange: any) {
  return {
    state: initialChatState, closed: false, busy: false,
    toolVerbosity: 'silent' as const, thinkingVerbosity: 'hidden' as const,
    showFileUpload: false, maxUploadBytes: 0, fileDownload: false,
    onSend: async () => true, onInterrupt: () => {}, onPermission: () => {},
    onFileDownload: () => {}, controls, onControlChange,
  };
}

describe('SessionControls renderer', () => {
  it('renders one control per kind with testids', () => {
    render(<ConversationView {...base(vi.fn())} />);
    expect(screen.getByTestId('control-model')).toBeTruthy();
    expect(screen.getByTestId('control-thinking')).toBeTruthy();
    expect(screen.getByTestId('control-wide')).toBeTruthy();
  });
  it('segmented change fires onControlChange(id, value)', () => {
    const cb = vi.fn();
    render(<ConversationView {...base(cb)} />);
    fireEvent.click(screen.getByText('High'));
    expect(cb).toHaveBeenCalledWith('thinking', 'high');
  });
  it('disabled select option shows whyDisabled tooltip title', async () => {
    render(<ConversationView {...base(vi.fn())} />);
    fireEvent.mouseDown(screen.getByTestId('control-model').querySelector('.ant-select-selector')!);
    await waitFor(() => expect(screen.getByText('B')).toBeTruthy());
    const opt = screen.getByText('B').closest('.ant-select-item');
    expect(opt?.getAttribute('title')).toBe('plan-gated');
  });

  it('renders a slider control with a control-reasoning_effort testid', () => {
    const sliderControls: SessionControl[] = [
      { id: 'reasoning_effort', kind: 'slider', label: 'Effort', value: 'low',
        levels: ['low', 'medium', 'high'] },
    ];
    render(<ConversationView {...{ ...base(vi.fn()), controls: sliderControls }} />);
    expect(screen.getByTestId('control-reasoning_effort')).toBeTruthy();
  });

  it('slider change fires onControlChange(id, level)', () => {
    const cb = vi.fn();
    const sliderControls: SessionControl[] = [
      { id: 'reasoning_effort', kind: 'slider', label: 'Effort', value: 'low',
        levels: ['low', 'medium', 'high'] },
    ];
    render(<ConversationView {...{ ...base(cb), controls: sliderControls }} />);
    // The handle carries role="slider"; ArrowRight advances to the next mark,
    // standing in for a drag — the branch maps the new index back to its level.
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowRight' });
    expect(cb).toHaveBeenCalledWith('reasoning_effort', 'medium');
  });

  it('a single-level / disabled slider is locked and hover explains why', async () => {
    const locked: SessionControl[] = [
      { id: 'reasoning_effort', kind: 'slider', label: 'Effort', value: 'high',
        levels: ['high'], disabled: true, whyDisabled: 'always on' },
    ];
    render(<ConversationView {...{ ...base(vi.fn()), controls: locked }} />);
    expect(screen.getByTestId('control-reasoning_effort').className).toContain('ant-slider-disabled');
    fireEvent.mouseEnter(screen.getByText('Effort'));
    await waitFor(() => expect(screen.getByText('always on')).toBeTruthy());
  });

  it('a control-level disabled flag grays the control and hover explains why', async () => {
    const locked: SessionControl[] = [
      { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'on',
        levels: ['on'], disabled: true, whyDisabled: 'always on' },
    ];
    render(<ConversationView {...{ ...base(vi.fn()), controls: locked }} />);
    // grayed: antd Segmented carries the disabled class
    expect(screen.getByTestId('control-thinking').className).toContain('ant-segmented-disabled');
    // hover the (enabled) labeled wrapper -> tooltip explains why
    fireEvent.mouseEnter(screen.getByText('Thinking'));
    await waitFor(() => expect(screen.getByText('always on')).toBeTruthy());
  });
});
