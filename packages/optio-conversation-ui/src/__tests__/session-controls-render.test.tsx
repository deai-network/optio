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

/** Where a pointer on a control lands: the tooltip anchor vultus wraps it in. */
function hoverTarget(id: string): HTMLElement {
  return screen.getByTestId(`control-${id}`).querySelector<HTMLElement>('span[style]')!;
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

  it('slider change fires onControlChange(id, level), once per move', () => {
    const cb = vi.fn();
    const sliderControls: SessionControl[] = [
      { id: 'reasoning_effort', kind: 'slider', label: 'Effort', value: 'low',
        levels: ['low', 'medium', 'high'] },
    ];
    render(<ConversationView {...{ ...base(cb), controls: sliderControls }} />);
    // The handle carries role="slider"; ArrowRight advances to the next mark
    // on keydown, and the move ends (one request) on keyup, as with a drag's
    // release. rc-slider's key handler reads event.keyCode, so pass it for jsdom.
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowRight', keyCode: 39 });
    expect(cb).not.toHaveBeenCalled();
    fireEvent.keyUp(screen.getByRole('slider'), { key: 'ArrowRight', keyCode: 39 });
    expect(cb).toHaveBeenCalledTimes(1);
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
    fireEvent.mouseEnter(hoverTarget('reasoning_effort'));
    await waitFor(() => expect(screen.getByText('always on')).toBeTruthy());
  });

  it('a control-level disabled flag grays the control and hover explains why', async () => {
    const locked: SessionControl[] = [
      { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'on',
        levels: ['on'], disabled: true, whyDisabled: 'always on' },
    ];
    render(<ConversationView {...{ ...base(vi.fn()), controls: locked }} />);
    // grayed: antd Segmented carries the disabled class
    expect(screen.getByTestId('control-thinking').querySelector('.ant-segmented-disabled')).toBeTruthy();
    // hover the control -> its tooltip explains why
    fireEvent.mouseEnter(hoverTarget('thinking'));
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

describe('compact controls bar', () => {
  const described: SessionControl[] = [
    { id: 'wide', kind: 'boolean', label: 'Wide', value: false,
      description: 'Should the agent use the wide layout?' },
    { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'low', levels: ['low', 'high'],
      description: 'How much should the agent think?' },
    { id: 'reasoning_effort', kind: 'slider', label: 'Effort', value: 'low',
      levels: ['low', 'medium', 'high'], description: 'How hard should the model think?' },
  ];
  const view = (cb = vi.fn()) => render(<ConversationView {...{ ...base(cb), controls: described }} />);

  it('a boolean control is a switch named by its label; a click asks for the other value', () => {
    const cb = vi.fn();
    view(cb);
    fireEvent.click(screen.getByRole('switch', { name: 'Wide' }));
    expect(cb).toHaveBeenCalledWith('wide', true);
  });

  it('a boolean control shows its description on hover', async () => {
    view();
    fireEvent.mouseEnter(hoverTarget('wide'));
    await waitFor(() => expect(screen.getByText('Should the agent use the wide layout?')).toBeTruthy());
  });

  it('a segmented control is named by its label and described by its description', () => {
    view();
    const group = within(screen.getByTestId('control-thinking')).getByLabelText('Thinking');
    expect(group.getAttribute('aria-description')).toContain('How much should the agent think?');
  });

  it("the slider's handle is named by the label and shows the description on hover", async () => {
    view();
    const handle = screen.getByRole('slider', { name: 'Effort' });
    fireEvent.mouseEnter(handle);
    await waitFor(() => expect(screen.getByText('How hard should the model think?')).toBeTruthy());
  });

  describe('labels', () => {
    const widths = (scroll: number, client: number) => {
      const own = (el: HTMLElement) => el.dataset.testid === 'session-toolbar';
      const scrollDesc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollWidth');
      const clientDesc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
      Object.defineProperty(HTMLElement.prototype, 'scrollWidth', {
        configurable: true,
        // Without the compact class the labels take room: the content is wider.
        get() { return own(this) ? (this.classList.contains('optio-cc-compact') ? client - 1 : scroll) : 0; },
      });
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
        configurable: true, get() { return own(this) ? client : 0; },
      });
      return () => {
        if (scrollDesc) Object.defineProperty(HTMLElement.prototype, 'scrollWidth', scrollDesc);
        if (clientDesc) Object.defineProperty(HTMLElement.prototype, 'clientWidth', clientDesc);
      };
    };

    it('each control is led by its label', () => {
      view();
      const labels = Array.from(document.querySelectorAll('.optio-cc-control-label')).map((l) => l.textContent);
      expect(labels).toEqual(['Wide', 'Thinking', 'Effort']);
    });

    it('shown while the toolbar fits', () => {
      const restore = widths(400, 600);
      try {
        view();
        expect(screen.getByTestId('session-toolbar').classList.contains('optio-cc-compact')).toBe(false);
      } finally { restore(); }
    });

    it('hidden (compact) when they would overflow it; the controls keep their names', () => {
      const restore = widths(800, 600);
      try {
        view();
        expect(screen.getByTestId('session-toolbar').classList.contains('optio-cc-compact')).toBe(true);
        expect(screen.getByRole('switch', { name: 'Wide' })).toBeTruthy();
        expect(screen.getByRole('slider', { name: 'Effort' })).toBeTruthy();
      } finally { restore(); }
    });
  });
});

describe('select option groups', () => {
  it('options with a group are listed after the others, under its heading', async () => {
    const grouped: SessionControl[] = [
      { id: 'model', kind: 'select', label: 'Model', value: 'opus',
        options: [{ value: 'opus', label: 'Opus 5.5' },
                  { value: 'claude-opus-5', label: 'Opus 5', group: 'Older versions' },
                  { value: 'sonnet', label: 'Sonnet 5.5' }] },
    ];
    render(<ConversationView {...{ ...base(vi.fn()), controls: grouped }} />);
    await openSelect('model');
    const rows = Array.from(document.querySelectorAll('.ant-select-item')).map((r) => (
      r.classList.contains('ant-select-item-group') ? `## ${r.textContent}` : r.textContent));
    expect(rows).toEqual(['Opus 5.5', 'Sonnet 5.5', '## Older versions', 'Opus 5']);
  });
});

describe('select controls with inline descriptions', () => {
  it('the open list shows each option\'s description under its label', async () => {
    const models: SessionControl[] = [
      { id: 'model', kind: 'select', label: 'Model', value: 'opus', inlineDescriptions: true,
        options: [{ value: 'opus', label: 'Opus 5.5', description: 'For complex work' },
                  { value: 'sonnet', label: 'Sonnet 5.5', description: 'Most efficient for simpler tasks' }] },
    ];
    render(<ConversationView {...{ ...base(vi.fn()), controls: models }} />);
    await openSelect('model');
    const descriptions = Array.from(document.querySelectorAll('[data-choice-part="description"]')).map((d) => d.textContent);
    expect(descriptions).toEqual(['For complex work', 'Most efficient for simpler tasks']);
  });

  it('without the flag, descriptions stay in tooltips', async () => {
    render(<ConversationView {...base(vi.fn())} />);
    await openSelect('model');
    expect(document.querySelector('[data-choice-part="description"]')).toBeNull();
  });
});
