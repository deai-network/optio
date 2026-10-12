import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { ComponentType } from 'react';
import { registerWidget } from 'optio-ui';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { ConversationWidget, registerConversationWidget } from '../ConversationWidget.js';
import { ConversationWidenessContext } from '../wideness.js';

// registerConversationWidget's registration is captured (optio-ui keeps its
// registry lookup private).
vi.mock('optio-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('optio-ui')>()),
  registerWidget: vi.fn(),
}));

// The conversation's reading width (owner, 2026-10-12): the host decides,
// through the registered widget's `wideness`: 'wide' (always the full width),
// 'narrow' (always the capped reading column; the default), or 'switch' (the
// Wide switch in the header, as before).

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
    state: { items: [{ kind: 'user', text: 'hi', seq: 1 }], busy: false, closed: false, controls: [] },
    closed: false, busy: false, toolVerbosity: 'verbose', thinkingVerbosity: 'visible',
    showFileUpload: false, maxUploadBytes: 1, fileDownload: false,
    onSend: vi.fn(async () => true), onInterrupt: vi.fn(), onPermission: vi.fn(), onFileDownload: vi.fn(),
    ...over,
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const widgetProps: any = {
  process: { _id: 'p1', name: 'n', widgetData: {}, status: { state: 'running' } },
  apiBaseUrl: '', widgetProxyUrl: '/api/widget/db/gm/p1/', prefix: 'gm', database: 'db',
};

const width = () => screen.getByTestId('conversation-content').style.maxWidth;

describe('wideness', () => {
  it('narrow by default: no Wide switch, the reading column capped', () => {
    render(<ConversationView {...viewProps()} />);
    expect(screen.queryByTestId('wide-toggle')).toBeNull();
    expect(width()).toBe('880px');
    // Nothing else in the header: no empty row.
    expect(screen.queryByTestId('conversation-header')).toBeNull();
  });

  it("'wide': no switch, the full width", () => {
    render(<ConversationView {...viewProps({ wideness: 'wide' })} />);
    expect(screen.queryByTestId('wide-toggle')).toBeNull();
    expect(width()).toBe('100%');
  });

  it("'switch': the Wide switch widens and narrows it", () => {
    render(<ConversationView {...viewProps({ wideness: 'switch' })} />);
    expect(width()).toBe('880px');
    fireEvent.click(screen.getByTestId('wide-toggle'));
    expect(width()).toBe('100%');
  });

  it('ConversationWidget hands its wideness to the view (narrow when absent)', () => {
    const { unmount } = render(<ConversationWidget {...widgetProps} />);
    expect(width()).toBe('880px');
    expect(screen.queryByTestId('wide-toggle')).toBeNull();
    unmount();
    render(<ConversationWidget {...widgetProps} wideness="wide" />);
    expect(width()).toBe('100%');
  });

  // A host can also change it live (owner, 2026-10-12: the demo's switch
  // reloaded the page): a ConversationWidget without its own wideness, and
  // one registered without it, take the host's ConversationWidenessContext.
  it("a ConversationWidget without its own wideness follows the host's context, live", () => {
    const view = render(
      <ConversationWidenessContext.Provider value="narrow"><ConversationWidget {...widgetProps} /></ConversationWidenessContext.Provider>,
    );
    expect(width()).toBe('880px');
    const content = screen.getByTestId('conversation-content');
    view.rerender(
      <ConversationWidenessContext.Provider value="wide"><ConversationWidget {...widgetProps} /></ConversationWidenessContext.Provider>,
    );
    expect(width()).toBe('100%');
    // The same conversation, not a remount.
    expect(screen.getByTestId('conversation-content')).toBe(content);
  });

  it("its own wideness wins over the host's context", () => {
    render(
      <ConversationWidenessContext.Provider value="wide"><ConversationWidget {...widgetProps} wideness="narrow" /></ConversationWidenessContext.Provider>,
    );
    expect(width()).toBe('880px');
  });

  it("registered without a wideness, the widget follows the host's context", () => {
    registerConversationWidget();
    const [, Widget] = vi.mocked(registerWidget).mock.calls.at(-1)!;
    const Registered = Widget as ComponentType<typeof widgetProps>;
    render(<ConversationWidenessContext.Provider value="switch"><Registered {...widgetProps} /></ConversationWidenessContext.Provider>);
    expect(screen.getByTestId('wide-toggle')).toBeTruthy();
  });

  it('registerConversationWidget({ wideness }) registers a widget with it', () => {
    registerConversationWidget({ wideness: 'switch' });
    const [name, Widget] = vi.mocked(registerWidget).mock.calls.at(-1)!;
    expect(name).toBe('conversation');
    const Registered = Widget as ComponentType<typeof widgetProps>;
    render(<Registered {...widgetProps} />);
    expect(screen.getByTestId('wide-toggle')).toBeTruthy();
  });
});
