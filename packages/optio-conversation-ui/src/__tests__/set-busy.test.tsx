import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import type { ComponentType } from 'react';
import { registerWidget } from 'optio-ui';
import { ConversationView, type ConversationViewProps } from '../ConversationView.js';
import { ConversationWidget, registerConversationWidget } from '../ConversationWidget.js';
import { ConversationSetBusyContext } from '../busy.js';

// registerConversationWidget's registration is captured (optio-ui keeps its
// registry lookup private).
vi.mock('optio-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('optio-ui')>()),
  registerWidget: vi.fn(),
}));

// The agent's busy / idle state for the host app (owner, 2026-10-12): an
// optional setBusy(bool) callback, as ConversationWidget's prop, as
// registerConversationWidget's option, or through the host's
// ConversationSetBusyContext.

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

describe('setBusy', () => {
  it('the view reports busy while the agent works on a turn, idle otherwise and when it goes away', () => {
    const setBusy = vi.fn();
    const wrap = (over: Partial<ConversationViewProps>) => (
      <ConversationSetBusyContext.Provider value={setBusy}><ConversationView {...viewProps(over)} /></ConversationSetBusyContext.Provider>
    );
    const view = render(wrap({}));
    expect(setBusy.mock.calls).toEqual([[false]]);
    view.rerender(wrap({ busy: true }));
    expect(setBusy.mock.calls).toEqual([[false], [true]]);
    // Only changes are reported.
    view.rerender(wrap({ busy: true, toolVerbosity: 'silent' }));
    expect(setBusy).toHaveBeenCalledTimes(2);
    // A closed conversation is idle.
    view.rerender(wrap({ busy: true, closed: true }));
    expect(setBusy.mock.calls.at(-1)).toEqual([false]);
    view.rerender(wrap({ busy: true }));
    expect(setBusy.mock.calls.at(-1)).toEqual([true]);
    setBusy.mockClear();
    view.unmount();
    expect(setBusy.mock.calls).toEqual([[false]]);
  });

  it("ConversationWidget's setBusy reaches the view, over the host's context", () => {
    const own = vi.fn();
    const host = vi.fn();
    render(
      <ConversationSetBusyContext.Provider value={host}><ConversationWidget {...widgetProps} setBusy={own} /></ConversationSetBusyContext.Provider>,
    );
    expect(own).toHaveBeenCalledWith(false);
    expect(host).not.toHaveBeenCalled();
  });

  it("without one, ConversationWidget reports to the host's context", () => {
    const host = vi.fn();
    render(<ConversationSetBusyContext.Provider value={host}><ConversationWidget {...widgetProps} /></ConversationSetBusyContext.Provider>);
    expect(host).toHaveBeenCalledWith(false);
  });

  it('registerConversationWidget({ setBusy }) registers a widget that reports to it', () => {
    const setBusy = vi.fn();
    registerConversationWidget({ setBusy });
    const [, Widget] = vi.mocked(registerWidget).mock.calls.at(-1)!;
    const Registered = Widget as ComponentType<typeof widgetProps>;
    render(<Registered {...widgetProps} />);
    expect(setBusy).toHaveBeenCalledWith(false);
  });
});
