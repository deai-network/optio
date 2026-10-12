import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import huHU from 'antd/locale/hu_HU';
import frFR from 'antd/locale/fr_FR';
import { ConversationView } from '../ConversationView';
import { resolveText } from '../text';
import { initialChatState, type CustomControl, type SessionControl } from '../chat';

// The data chat's control (Task 6 defines it): a language-map caption, plain
// option labels, one option described per language, the other plainly.
const SPEED: CustomControl = {
  id: 'speed',
  label: { en: 'Model', hu: 'Modell', de: 'Modell' },
  description: null,
  options: [
    { value: 'fast', label: 'Fast', description: { en: 'Quick answers', hu: 'Gyors válaszok' } },
    { value: 'extensive', label: 'Extensive', description: 'Thorough answers' },
  ],
  value: 'extensive',
};

const TONE: CustomControl = {
  id: 'tone',
  label: 'Tone',
  description: 'How the agent writes',
  options: [
    { value: 'concise', label: 'Concise', description: null },
    { value: 'detailed', label: 'Detailed', description: null },
  ],
  value: 'concise',
};

const BUILT_IN: SessionControl[] = [
  { id: 'thinking', kind: 'segmented', label: 'Thinking', value: 'low', levels: ['low', 'high'] },
];

function base(over: Record<string, unknown> = {}) {
  return {
    state: initialChatState, closed: false, busy: false,
    toolVerbosity: 'silent' as const, thinkingVerbosity: 'hidden' as const,
    showFileUpload: false, maxUploadBytes: 0, fileDownload: false,
    onSend: async () => true, onInterrupt: () => {}, onPermission: () => {},
    onFileDownload: () => {},
    customControls: [SPEED],
    onCustomControlChange: vi.fn(async () => null),
    ...over,
  };
}

/** The label of a custom control's highlighted segment, or null when none is. */
function selected(id: string): string | null {
  const radios = Array.from(screen.getByTestId(`custom-control-${id}`).querySelectorAll<HTMLInputElement>('input[type=radio]'));
  const on = radios.find((r) => r.checked);
  return on ? on.closest('label')!.textContent : null;
}

// A choice's description in its segment's tooltip: alone, or after the lead
// newer vultus-antd puts before it ("Switch to:" on another choice, "Current
// state:" on the current one).
function described(text: string): RegExp {
  return new RegExp(`(^|: )${text}$`);
}

function captions(): (string | null)[] {
  return Array.from(document.querySelectorAll('.optio-cc-control-label')).map((l) => l.textContent);
}

describe('custom controls in the session toolbar', () => {
  it('one small OneOfSegmented per control, before the built-in controls', () => {
    render(<ConversationView {...base({ customControls: [SPEED, TONE], controls: BUILT_IN, onControlChange: vi.fn() })} />);
    const toolbar = screen.getByTestId('session-toolbar');
    for (const id of ['speed', 'tone']) {
      const wrapper = within(toolbar).getByTestId(`custom-control-${id}`);
      const segmented = wrapper.querySelectorAll('.ant-segmented');
      expect(segmented).toHaveLength(1);
      expect(segmented[0].classList.contains('ant-segmented-sm')).toBe(true);
    }
    const tone = screen.getByTestId('custom-control-tone');
    const builtIn = screen.getByTestId('control-thinking');
    expect(tone.compareDocumentPosition(builtIn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(captions()).toEqual(['Model', 'Tone', 'Thinking']);
  });

  it('highlights the current option; no segment for a null value', () => {
    const { unmount } = render(<ConversationView {...base()} />);
    expect(selected('speed')).toBe('Extensive');
    unmount();
    render(<ConversationView {...base({ customControls: [{ ...SPEED, value: null }] })} />);
    expect(selected('speed')).toBeNull();
  });

  it("an option's description is its tooltip", async () => {
    render(<ConversationView {...base()} />);
    fireEvent.mouseEnter(screen.getByText('Fast').parentElement!);
    await waitFor(() => expect(screen.getByText(described('Quick answers'))).toBeTruthy());
  });

  it('the segmented control is named by its label and described by its description', () => {
    render(<ConversationView {...base({ customControls: [TONE] })} />);
    const group = within(screen.getByTestId('custom-control-tone')).getByLabelText('Tone');
    expect(group.getAttribute('aria-description')).toContain('How the agent writes');
  });

  it('shown without the built-in controls', () => {
    render(<ConversationView {...base()} />);
    expect(screen.getByTestId('custom-control-speed')).toBeTruthy();
    expect(screen.queryByTestId('control-thinking')).toBeNull();
  });

  it('nothing for an empty list', () => {
    render(<ConversationView {...base({ customControls: [] })} />);
    expect(screen.queryByTestId('custom-control-speed')).toBeNull();
    expect(captions()).toEqual([]);
  });

  it('disabled while a turn runs', () => {
    render(<ConversationView {...base({ busy: true })} />);
    expect(screen.getByTestId('custom-control-speed').querySelector('.ant-segmented-disabled')).toBeTruthy();
  });

  it('disabled once the conversation is closed', () => {
    render(<ConversationView {...base({ state: { ...initialChatState, closed: true }, closed: true })} />);
    expect(screen.getByTestId('custom-control-speed').querySelector('.ant-segmented-disabled')).toBeTruthy();
  });

  it('picking an option calls onCustomControlChange(id, value)', async () => {
    const onCustomControlChange = vi.fn(async () => null);
    render(<ConversationView {...base({ onCustomControlChange })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(onCustomControlChange).toHaveBeenCalledWith('speed', 'fast'));
    expect(screen.queryByTestId('conversation-error')).toBeNull();
  });

  it('locked while the pick is in flight', async () => {
    let finish!: (failure: string | null) => void;
    const onCustomControlChange = vi.fn(() => new Promise<string | null>((resolve) => { finish = resolve; }));
    render(<ConversationView {...base({ onCustomControlChange })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(screen.getByTestId('custom-control-speed').querySelector('.ant-segmented-disabled')).toBeTruthy());
    finish(null);
    await waitFor(() => expect(screen.getByTestId('custom-control-speed').querySelector('.ant-segmented-disabled')).toBeNull());
  });

  it("a failed pick shows why in the input bar's error slot", async () => {
    const onCustomControlChange = vi.fn(async () => 'Switch failed: no such model');
    render(<ConversationView {...base({ onCustomControlChange })} />);
    fireEvent.click(screen.getByText('Fast'));
    await waitFor(() => expect(screen.getByTestId('conversation-error').textContent).toContain('Switch failed: no such model'));
  });

  describe('compact toolbar', () => {
    // `scroll`: the toolbar's content width with its captions shown (a
    // number, or computed from the toolbar); compact, it always fits.
    const widths = (scroll: number | ((toolbar: HTMLElement) => number), client: number) => {
      const own = (el: HTMLElement) => el.dataset.testid === 'session-toolbar';
      const scrollDesc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollWidth');
      const clientDesc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
      Object.defineProperty(HTMLElement.prototype, 'scrollWidth', {
        configurable: true,
        get() {
          if (!own(this)) return 0;
          if (this.classList.contains('optio-cc-compact')) return client - 1;
          return typeof scroll === 'number' ? scroll : scroll(this);
        },
      });
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
        configurable: true, get() { return own(this) ? client : 0; },
      });
      return () => {
        if (scrollDesc) Object.defineProperty(HTMLElement.prototype, 'scrollWidth', scrollDesc);
        if (clientDesc) Object.defineProperty(HTMLElement.prototype, 'clientWidth', clientDesc);
      };
    };

    it('captions shown while the toolbar fits', () => {
      const restore = widths(400, 600);
      try {
        render(<ConversationView {...base()} />);
        const caption = document.querySelector<HTMLElement>('.optio-cc-control-label')!;
        expect(caption.textContent).toBe('Model');
        expect(getComputedStyle(caption).display).not.toBe('none');
      } finally { restore(); }
    });

    it('captions hidden when they would overflow it; the control keeps its name', () => {
      const restore = widths(800, 600);
      try {
        render(<ConversationView {...base()} />);
        expect(screen.getByTestId('session-toolbar').classList.contains('optio-cc-compact')).toBe(true);
        const caption = document.querySelector<HTMLElement>('.optio-cc-control-label')!;
        expect(getComputedStyle(caption).display).toBe('none');
        expect(within(screen.getByTestId('custom-control-speed')).getByLabelText('Model')).toBeTruthy();
      } finally { restore(); }
    });

    it('measured again when the custom controls change', () => {
      // Each caption takes 300px of the 600px toolbar.
      const restore = widths((toolbar) => 300 * toolbar.querySelectorAll('.optio-cc-control-label').length, 600);
      try {
        const { rerender } = render(<ConversationView {...base()} />);
        const toolbar = screen.getByTestId('session-toolbar');
        expect(toolbar.classList.contains('optio-cc-compact')).toBe(false);
        rerender(<ConversationView {...base({ customControls: [SPEED, TONE, { ...TONE, id: 'tone2' }] })} />);
        expect(toolbar.classList.contains('optio-cc-compact')).toBe(true);
      } finally { restore(); }
    });
  });
});

// A control may have no label (owner request 2026-10-12): no caption; its
// description, else its id, names it.
describe('a control without a label', () => {
  const CAPTIONLESS: CustomControl = {
    ...SPEED,
    label: null,
    description: {
      en: 'What should be the balance between detail level and speed?',
      hu: 'Mi legyen az egyensúly a részletesség és a gyorsaság között?',
    },
  };

  it('shows no caption; its description is its accessible name', () => {
    render(<ConversationView {...base({ customControls: [CAPTIONLESS] })} />);
    expect(captions()).toEqual([]);
    const wrapper = screen.getByTestId('custom-control-speed');
    expect(within(wrapper).getByLabelText('What should be the balance between detail level and speed?')).toBeTruthy();
  });

  it('its description in the UI language', () => {
    render(<ConfigProvider locale={huHU}><ConversationView {...base({ customControls: [CAPTIONLESS] })} /></ConfigProvider>);
    expect(captions()).toEqual([]);
    const wrapper = screen.getByTestId('custom-control-speed');
    expect(within(wrapper).getByLabelText('Mi legyen az egyensúly a részletesség és a gyorsaság között?')).toBeTruthy();
  });

  it('named by its id without a description', () => {
    render(<ConversationView {...base({ customControls: [{ ...CAPTIONLESS, description: null }] })} />);
    expect(captions()).toEqual([]);
    expect(within(screen.getByTestId('custom-control-speed')).getByLabelText('speed')).toBeTruthy();
  });

  it('next to a labelled control, only that one has a caption', () => {
    render(<ConversationView {...base({ customControls: [CAPTIONLESS, TONE] })} />);
    expect(captions()).toEqual(['Tone']);
  });
});

describe('custom control texts follow the UI language', () => {
  it('the language entry (hu)', async () => {
    render(<ConfigProvider locale={huHU}><ConversationView {...base()} /></ConfigProvider>);
    expect(captions()).toEqual(['Modell']);
    fireEvent.mouseEnter(screen.getByText('Fast').parentElement!);
    await waitFor(() => expect(screen.getByText(described('Gyors válaszok'))).toBeTruthy());
  });

  it('a language with no entry falls back to en (fr)', async () => {
    render(<ConfigProvider locale={frFR}><ConversationView {...base()} /></ConfigProvider>);
    expect(captions()).toEqual(['Model']);
    fireEvent.mouseEnter(screen.getByText('Fast').parentElement!);
    await waitFor(() => expect(screen.getByText(described('Quick answers'))).toBeTruthy());
  });

  it('no locale: en', () => {
    render(<ConversationView {...base()} />);
    expect(captions()).toEqual(['Model']);
  });

  it('plain strings are shown as they are, in any language', async () => {
    render(<ConfigProvider locale={huHU}><ConversationView {...base()} /></ConfigProvider>);
    expect(screen.getByText('Fast')).toBeTruthy();
    expect(screen.getByText('Extensive')).toBeTruthy();
    fireEvent.mouseEnter(screen.getByText('Extensive').parentElement!);
    await waitFor(() => expect(screen.getByText(described('Thorough answers'))).toBeTruthy());
  });
});

describe('resolveText', () => {
  it('a plain string as it is', () => {
    expect(resolveText('Fast', 'hu')).toBe('Fast');
  });
  it("the language's entry", () => {
    expect(resolveText({ en: 'Model', hu: 'Modell' }, 'hu')).toBe('Modell');
  });
  it('else the en entry', () => {
    expect(resolveText({ hu: 'Modell', en: 'Model' }, 'fr')).toBe('Model');
  });
  it('else the first entry', () => {
    expect(resolveText({ hu: 'Modell', de: 'Modell (de)' }, 'fr')).toBe('Modell');
  });
});
