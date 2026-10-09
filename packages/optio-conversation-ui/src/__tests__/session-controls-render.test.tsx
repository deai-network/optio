import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { ConversationView } from '../ConversationView';
import { initialChatState, SessionControl } from '../chat';

const controls: SessionControl[] = [
  { id: 'model', kind: 'select', label: 'Model', value: 'a',
    options: [{ value: 'a', label: 'A' },
              { value: 'b', label: 'B', disabled: true, whyDisabled: 'plan-gated' }] },
  { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'low', levels: ['low', 'high'] },
  { id: 'wide', kind: 'boolean', label: 'Wide', value: false },
];

/** Opens a select control's list (vultus OneOfSelect inside its control-<id> wrapper). */
async function openSelect(id: string) {
  fireEvent.mouseDown(within(screen.getByTestId(`control-${id}`)).getByRole('combobox'));
  await waitFor(() => expect(document.querySelector('.ant-select-item-option')).toBeTruthy());
}

/** An option row of the open list, by its text. */
function option(text: string): HTMLElement {
  return Array.from(document.querySelectorAll<HTMLElement>('.ant-select-item-option')).find((o) => o.textContent === text)!;
}

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
  it('disabled select option: its reason on hover (no native title), not choosable', async () => {
    const cb = vi.fn();
    render(<ConversationView {...base(cb)} />);
    await openSelect('model');
    const opt = option('B');
    expect(opt.getAttribute('title')).toBeFalsy();
    fireEvent.mouseEnter(opt.querySelector('span[style]')!);
    await waitFor(() => expect(screen.getByText('plan-gated')).toBeTruthy());
    fireEvent.click(opt);
    expect(cb).not.toHaveBeenCalled();
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
    // rc-slider's key handler reads event.keyCode, so pass it for jsdom.
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowRight', keyCode: 39 });
    expect(cb).toHaveBeenCalledWith('reasoning_effort', 'medium');
  });

  it('a single-level / disabled slider is locked and hover explains why', async () => {
    const locked: SessionControl[] = [
      { id: 'reasoning_effort', kind: 'slider', label: 'Effort', value: 'high',
        levels: ['high'], disabled: true, whyDisabled: 'always on' },
    ];
    render(<ConversationView {...{ ...base(vi.fn()), controls: locked }} />);
    // The testid rides a wrapping span; the disabled state lives on the
    // .ant-slider inside it.
    expect(screen.getByTestId('control-reasoning_effort').querySelector('.ant-slider')!.className)
      .toContain('ant-slider-disabled');
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

describe('select controls as vultus one-of selects', () => {
  const modes: SessionControl[] = [
    { id: 'permission_mode', kind: 'select', label: 'Permissions', value: 'acceptEdits',
      description: 'How Claude asks before it acts.',
      options: [
        { value: 'acceptEdits', label: 'Accept edits', description: 'Edits files without asking' },
        { value: 'auto', label: 'Auto', description: 'A classifier reviews **each** action' },
        { value: 'bypassPermissions', label: 'Bypass', description: 'Runs everything', variant: 'danger',
          confirm: 'Switch to Bypass?' },
      ] },
  ];

  it('choosing an option fires onControlChange(id, value)', async () => {
    const cb = vi.fn();
    render(<ConversationView {...{ ...base(cb), controls: modes }} />);
    await openSelect('permission_mode');
    fireEvent.click(option('Auto'));
    await waitFor(() => expect(cb).toHaveBeenCalledWith('permission_mode', 'auto'));
  });

  it('hovering an option shows its description, as markdown', async () => {
    render(<ConversationView {...{ ...base(vi.fn()), controls: modes }} />);
    await openSelect('permission_mode');
    fireEvent.mouseEnter(option('Auto').querySelector('span[style]')!);
    await waitFor(() => expect(screen.getByText('each', { selector: 'strong' })).toBeTruthy());
  });

  it('the closed select: the control description, then the current option as its current state', async () => {
    render(<ConversationView {...{ ...base(vi.fn()), controls: modes }} />);
    fireEvent.mouseEnter(screen.getByTestId('control-permission_mode').querySelector('span[style]')!);
    await waitFor(() => expect(screen.getByText('How Claude asks before it acts.')).toBeTruthy());
    expect(screen.getByText('Current state: Edits files without asking')).toBeTruthy();
  });

  it('a danger option is styled danger and its confirmation asks first', async () => {
    const cb = vi.fn();
    render(<ConversationView {...{ ...base(cb), controls: modes }} />);
    await openSelect('permission_mode');
    const label = within(option('Bypass')).getByText('Bypass');
    expect(label.style.color).not.toBe('');
    fireEvent.click(option('Bypass'));
    expect(await screen.findByText('Switch to Bypass?')).toBeTruthy();
    expect(cb).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole('button').find((b) => b.textContent?.includes('OK'))!);
    await waitFor(() => expect(cb).toHaveBeenCalledWith('permission_mode', 'bypassPermissions'));
  });
});
