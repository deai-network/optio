import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { ConfigProvider, theme } from 'antd';
import huHU from 'antd/locale/hu_HU';
import { ConversationPlaceholder, ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { ConversationWidget } from '../ConversationWidget.js';

// Ant Design X experiment (owner-approved 2026-10-12): an agent task's
// optional `welcome` (optio-agents WelcomeCard, published as
// widgetData.welcome: title, markdown description, icon URL relative to the
// web app root) shows as an X Welcome card at the top of the transcript.

class MockEventSource {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  constructor(url: string) { this.url = url; }
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

beforeEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = MockEventSource;
});

function viewProps(over: Partial<ConversationViewProps> = {}): ConversationViewProps {
  return {
    state: { items: [{ kind: 'user', text: 'hi there', seq: 1 }], busy: false, closed: false, controls: [] },
    closed: false, busy: false, toolVerbosity: 'verbose', thinkingVerbosity: 'visible',
    showFileUpload: false, maxUploadBytes: 1, fileDownload: false,
    onSend: vi.fn(async () => true), onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload: vi.fn(),
    ...over,
  };
}

const WELCOME = { title: 'Claude Code conversation', description: 'Try **this**.', icon: '/welcome/optio.png' };
const card = () => document.querySelector('[data-testid="conversation-content"] .ant-welcome') as HTMLElement | null;

describe('the welcome card', () => {
  it('is absent without a welcome', () => {
    render(<ConversationView {...viewProps()} />);
    expect(card()).toBeNull();
  });

  it('shows at the top of the transcript: title, markdown description, the icon as an image', () => {
    render(<ConversationView {...viewProps({ welcome: WELCOME })} />);
    const c = card();
    expect(c).not.toBeNull();
    const content = screen.getByTestId('conversation-content');
    expect(content.firstElementChild?.contains(c)).toBe(true);
    expect(c!.textContent).toContain('Claude Code conversation');
    expect(c!.querySelector('strong')?.textContent).toBe('this');
    expect(c!.querySelector('img')?.getAttribute('src')).toBe('/welcome/optio.png');
    // Above the conversation itself.
    expect(c!.compareDocumentPosition(screen.getByText('hi there')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('a title alone is enough', () => {
    render(<ConversationView {...viewProps({ welcome: { title: 'Just a title', description: null, icon: null } })} />);
    expect(card()!.textContent).toContain('Just a title');
    expect(card()!.querySelector('img')).toBeNull();
  });

  it("ConversationWidget takes it from the process's widget data", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const props: any = {
      process: { _id: 'p1', name: 'n', widgetData: { welcome: WELCOME }, status: { state: 'running' } },
      apiBaseUrl: '', widgetProxyUrl: '/api/widget/db/gm/p1/', prefix: 'gm', database: 'db',
    };
    render(<ConversationWidget {...props} />);
    expect(card()?.textContent).toContain('Claude Code conversation');
  });

  // Owner, 2026-10-12: title and description may be language maps
  // (optio_agents.text.Text), resolved like the custom controls' texts; and a
  // minimum height lets a big icon fit.
  it("a language map resolves to antd's locale language, else 'en', else the first entry", () => {
    const title = { en: 'Welcome', hu: 'Üdv', de: 'Willkommen' };
    const view = render(
      <ConfigProvider locale={huHU}>
        <ConversationView {...viewProps({ welcome: { title, description: { en: 'Hi **all**', hu: 'Szia **mindenki**' } } })} />
      </ConfigProvider>,
    );
    expect(card()!.textContent).toContain('Üdv');
    expect(card()!.querySelector('strong')?.textContent).toBe('mindenki');
    view.unmount();
    render(<ConversationView {...viewProps({ welcome: { title } })} />);
    expect(card()!.textContent).toContain('Welcome');
  });

  it('a map without the language or en shows its first entry', () => {
    render(<ConversationView {...viewProps({ welcome: { title: { de: 'Willkommen', hu: 'Üdv' } } })} />);
    expect(card()!.textContent).toContain('Willkommen');
  });

  // Owner, 2026-10-12: the card resembles the agent's answers (their glass),
  // not X's flat fill (which looked like a user prompt without the glass).
  it("wears the agent answers' glass, light or dark with the theme", () => {
    const view = render(<ConversationView {...viewProps({ welcome: WELCOME })} />);
    expect(card()!.classList.contains('optio-x-glass')).toBe(true);
    expect(card()!.classList.contains('optio-x-glass-light')).toBe(true);
    expect(card()!.classList.contains('optio-x-glass-user')).toBe(false);
    // The glass's edge is an absolutely placed ring.
    expect(card()!.style.position).toBe('relative');
    // The answer bubbles' corners (X Bubble's default shape: twice the theme's
    // borderRadius), not the filled Welcome's borderRadiusLG.
    expect(card()!.style.borderRadius).toBe(`${2 * theme.getDesignToken().borderRadius}px`);
    view.unmount();
    render(
      <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
        <ConversationView {...viewProps({ welcome: WELCOME })} />
      </ConfigProvider>,
    );
    expect(card()!.classList.contains('optio-x-glass-dark')).toBe(true);
  });

  // Owner, 2026-10-12: the icon fills the card's height. X gives it a fixed
  // height (a title line and one description line), so it is measured: the
  // card's text (X's content wrapper) decides the height, the icon follows.
  describe('the icon fills the height', () => {
    let textHeight = 0;
    let observed: { el: Element; cb: () => void }[] = [];
    const icon = () => card()!.querySelector('.ant-welcome-icon') as HTMLElement;
    const reflow = (height: number) => {
      textHeight = height;
      act(() => observed.filter((o) => o.el.classList.contains('ant-welcome-content-wrapper')).forEach((o) => o.cb()));
    };

    beforeEach(() => {
      textHeight = 0;
      observed = [];
      vi.stubGlobal('ResizeObserver', class {
        cb: () => void;
        constructor(cb: () => void) { this.cb = cb; }
        observe(el: Element) { observed.push({ el, cb: this.cb }); }
        unobserve() {}
        disconnect() { observed = observed.filter((o) => o.cb !== this.cb); }
      });
      const original = Element.prototype.getBoundingClientRect;
      vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
        return this.classList.contains('ant-welcome-content-wrapper')
          ? ({ x: 0, y: 0, top: 0, left: 0, right: 600, bottom: textHeight, width: 600, height: textHeight, toJSON() {} } as DOMRect)
          : original.call(this);
      });
    });
    afterEach(() => {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it("is as tall as the card's text, and follows it as the text reflows", () => {
      textHeight = 102;
      render(<ConversationView {...viewProps({ welcome: WELCOME })} />);
      expect(icon().style.height).toBe('102px');
      reflow(150);
      expect(icon().style.height).toBe('150px');
      reflow(80);
      expect(icon().style.height).toBe('80px');
    });

    it('the text keeps its own height (top-aligned), so the icon can shrink with it', () => {
      render(<ConversationView {...viewProps({ welcome: WELCOME })} />);
      expect(card()!.style.alignItems).toBe('flex-start');
    });

    it("keeps its width beside long text, at most a quarter of the card's, so a narrow card keeps room for its text", () => {
      render(<ConversationView {...viewProps({ welcome: WELCOME })} />);
      expect(icon().style.flexShrink).toBe('0');
      expect(icon().style.maxWidth).toBe('25%');
    });

    // Owner, 2026-10-12: min_height is the image's size, not the card's.
    it("minHeight is the icon's minimum height, taller text making it taller", () => {
      textHeight = 102;
      render(<ConversationView {...viewProps({ welcome: { ...WELCOME, minHeight: 200 } })} />);
      expect(icon().style.height).toBe('200px');
      expect(card()!.style.minHeight).toBe('');
      reflow(250);
      expect(icon().style.height).toBe('250px');
    });

    it('with minHeight the width cap is at least that, so a narrow card keeps the size', () => {
      render(<ConversationView {...viewProps({ welcome: { ...WELCOME, minHeight: 200 } })} />);
      expect(icon().style.maxWidth).toBe('max(25%, 200px)');
    });

    it("unmeasured (no layout yet), the icon keeps X's own height", () => {
      render(<ConversationView {...viewProps({ welcome: WELCOME })} />);
      expect(card()!.style.minHeight).toBe('');
      expect(icon().style.height).toBe('');
    });
  });
});

// Owner, 2026-10-12: what a conversation shows before it exists (a chat that
// is starting) is the same welcome card, in the same place, so the
// conversation replacing it does not jump.
describe('ConversationPlaceholder', () => {
  const placeholderCard = () => document.querySelector('[data-testid="conversation-placeholder"] .ant-welcome') as HTMLElement | null;

  it('is the welcome card: the title, any nodes as icon and description, the same glass and corners', () => {
    render(
      <ConversationPlaceholder
        title="Starting chat"
        icon={<span data-testid="spin" />}
        description={<div data-testid="lines" />}
      />,
    );
    expect(placeholderCard()).not.toBeNull();
    expect(screen.getByText('Starting chat')).toBeTruthy();
    expect(placeholderCard()!.querySelector('[data-testid="spin"]')).not.toBeNull();
    expect(placeholderCard()!.querySelector('[data-testid="lines"]')).not.toBeNull();
    expect(placeholderCard()!.classList.contains('optio-x-glass')).toBe(true);
    expect(placeholderCard()!.style.borderRadius).toBe(`${2 * theme.getDesignToken().borderRadius}px`);
  });

  it("takes a welcome's minimum height", () => {
    render(<ConversationPlaceholder title="Starting chat" icon={<span />} minHeight={300} />);
    const icon = placeholderCard()!.querySelector('.ant-welcome-icon') as HTMLElement;
    expect(icon.style.height).toBe('300px');
    expect(icon.style.maxWidth).toBe('max(25%, 300px)');
  });

  it("sits in the transcript's reading column: narrow by default, the full width when wide", () => {
    const view = render(<ConversationPlaceholder title="Starting chat" />);
    expect(placeholderCard()!.parentElement!.style.maxWidth).toBe('880px');
    view.unmount();
    render(<ConversationPlaceholder title="Starting chat" wideness="wide" />);
    expect(placeholderCard()!.parentElement!.style.maxWidth).toBe('100%');
  });

  it('resolves a language map, as the welcome card does', () => {
    render(
      <ConfigProvider locale={huHU}>
        <ConversationPlaceholder title={{ en: 'Starting chat', hu: 'Chat indítása' }} />
      </ConfigProvider>,
    );
    expect(screen.getByText('Chat indítása')).toBeTruthy();
  });
});
