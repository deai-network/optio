import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, screen, within } from '@testing-library/react';
import React from 'react';
import { I18nextProvider } from 'react-i18next';
import i18next from 'i18next';
import { SpinnerScope, VultusComponentsContext, type SpinnerProps } from 'vultus-core';
import { _clearWidgetRegistry } from '../widgets/registry.js';

// ProcessDetailView streams its process; mock the hooks it uses, as ProcessDetailView.test.tsx does.
const mockProcessStream = vi.fn();
vi.mock('../hooks/useProcessStream.js', () => ({
  useProcessStream: (...args: any[]) => mockProcessStream(...args),
}));
vi.mock('../hooks/useProcessActions.js', () => ({
  useProcessActions: () => ({ launch: vi.fn(), cancel: vi.fn(), resurrect: vi.fn() }),
}));
vi.mock('../context/useOptioContext.js', () => ({
  useOptioBaseUrl: () => 'http://host',
  useOptioPrefix: () => 'optio',
  useOptioDatabase: () => 'mydb',
  useOptioClient: () => undefined,
  useOptioLive: () => false,
}));

// Import after mocks.
const { ProcessList } = await import('../components/ProcessList.js');
const { LaunchControls } = await import('../components/LaunchControls.js');
const { ProcessDetailView } = await import('../components/ProcessDetailView.js');
const { IframeWidget } = await import('../widgets/IframeWidget.js');

// antd's List pagination watches breakpoints (window.matchMedia); jsdom has none.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const calls: SpinnerProps[] = [];
function RecSpinner(p: SpinnerProps) {
  calls.push(p);
  return <span data-testid="rec-spinner" data-key={p.spinnerKey} />;
}

/** The props the lent Spinner last received for `key`. */
function propsOf(key: string): SpinnerProps | undefined {
  return calls.filter((c) => c.spinnerKey === key).at(-1);
}

const i18n = i18next.createInstance();
i18n.init({ lng: 'en', resources: { en: { translation: {} } } });

/** Wraps `node` in i18n and, when `withSpinner`, a VultusComponentsContext lending RecSpinner. */
function wrap(node: React.ReactNode, withSpinner = true) {
  return (
    <I18nextProvider i18n={i18n}>
      {withSpinner
        ? <VultusComponentsContext.Provider value={{ Spinner: RecSpinner }}>{node}</VultusComponentsContext.Provider>
        : node}
    </I18nextProvider>
  );
}

const lent = { Spinner: RecSpinner };
const none = {}; // VultusComponentsContext's default: nothing lent

/**
 * Like `wrap`, but the tree keeps its shape whether or not a Spinner is lent (only the context
 * value changes), so a rerender updates the component in place instead of remounting it.
 */
function stable(node: React.ReactNode, withSpinner: boolean) {
  return (
    <I18nextProvider i18n={i18n}>
      <VultusComponentsContext.Provider value={withSpinner ? lent : none}>{node}</VultusComponentsContext.Provider>
    </I18nextProvider>
  );
}

const resurrecting = {
  _id: '9', status: { state: 'failed' }, supportsResume: true, hasSavedState: true,
  supportsResurrect: true, hasUnsavedWork: true, resurrecting: true,
};

function iframeProps(overrides: Partial<any> = {}) {
  return {
    process: {
      _id: 'abc', processId: 'p', name: 'P',
      status: { state: 'running' }, progress: { percent: null },
      ...overrides,
    },
    apiBaseUrl: 'http://localhost:3000',
    widgetProxyUrl: 'http://localhost:3000/api/widget/abc/',
    prefix: 'optio',
  };
}

/** True when the lent spinner precedes the "Loading…" text inside `el`. */
function spinnerBeforeText(el: HTMLElement): boolean {
  const spinner = within(el).getByTestId('rec-spinner');
  const text = Array.from(el.childNodes).find((n) => n.nodeType === Node.TEXT_NODE && n.textContent === 'Loading…');
  if (!text) return false;
  return !!(spinner.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING);
}

beforeEach(() => {
  calls.length = 0;
  _clearWidgetRegistry();
  mockProcessStream.mockReset();
  mockProcessStream.mockReturnValue({ tree: null, logs: [], connected: false });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe('ProcessList — spinner slot', () => {
  it('loading: the provided Spinner is the List\'s indicator, keyed optio:process-list, size default', () => {
    const { container } = render(wrap(<ProcessList processes={[]} loading />));
    const spinner = screen.getByTestId('rec-spinner');
    expect(spinner.getAttribute('data-key')).toBe('optio:process-list');
    // Exactly its own props: nothing injected by antd (className, style, percent).
    expect(propsOf('optio:process-list')).toStrictEqual({ spinnerKey: 'optio:process-list', size: 'default' });
    expect(spinner.closest('.ant-spin-spinning')).not.toBeNull();
    // antd's injected className lands on optio's wrapper, which undoes .ant-spin-dot's 1em box.
    const indicator = spinner.parentElement as HTMLElement;
    expect(indicator.classList.contains('optio-spinner-slot-indicator')).toBe(true);
    expect(indicator.classList.contains('ant-spin-dot')).toBe(true);
    expect(indicator.style.width).toBe('auto');
    expect(indicator.style.height).toBe('auto');
    expect(indicator.style.display).toBe('inline-flex');
    // antd's own dot indicator is not rendered.
    expect(container.querySelector('.ant-spin-dot-holder')).toBeNull();
  });

  it('not loading: no Spinner rendered', () => {
    render(wrap(<ProcessList processes={[]} loading={false} />));
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });

  it('without a provider: antd\'s default indicator, unchanged', () => {
    const { container } = render(wrap(<ProcessList processes={[]} loading />, false));
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
    expect(container.querySelector('.ant-spin-spinning .ant-spin-dot-holder')).not.toBeNull();
  });
});

describe('LaunchControls — spinner slot', () => {
  it('resurrecting: the provided Spinner replaces LoadingOutlined, keyed to the process, size small', () => {
    const { container } = render(wrap(
      <LaunchControls process={resurrecting} onLaunch={vi.fn()} onResurrect={vi.fn()} size="small" />,
    ));
    const btn = screen.getByRole('button', { name: 'Resurrecting' });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(within(btn).getByTestId('rec-spinner').getAttribute('data-key')).toBe('optio:process:9:resurrecting');
    // Exactly its own props: nothing injected by antd (className, style, percent).
    expect(propsOf('optio:process:9:resurrecting')).toStrictEqual({ spinnerKey: 'optio:process:9:resurrecting', size: 'small' });
    expect(container.querySelector('.anticon-loading')).toBeNull();
  });

  it('not resurrecting: no Spinner rendered', () => {
    render(wrap(
      <LaunchControls process={{ ...resurrecting, resurrecting: false }} onLaunch={vi.fn()} onResurrect={vi.fn()} />,
    ));
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });

  it('without a provider: LoadingOutlined, unchanged', () => {
    const { container } = render(wrap(
      <LaunchControls process={resurrecting} onLaunch={vi.fn()} onResurrect={vi.fn()} size="small" />, false,
    ));
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
    const btn = screen.getByRole('button', { name: 'Resurrecting' });
    expect(btn.querySelector('.anticon-loading.anticon-spin')).not.toBeNull();
  });
});

describe('ProcessDetailView — spinner slot', () => {
  it('loading: the provided Spinner before "Loading…", keyed to the process, size small; test id unchanged', () => {
    render(wrap(<ProcessDetailView processId="abc" />));
    const loading = screen.getByTestId('optio-detail-loading');
    expect(within(loading).getByTestId('rec-spinner').getAttribute('data-key')).toBe('optio:process:abc:detail');
    // Exactly its own props: nothing injected by antd (className, style, percent).
    expect(propsOf('optio:process:abc:detail')).toStrictEqual({ spinnerKey: 'optio:process:abc:detail', size: 'small' });
    expect(loading.textContent).toBe('Loading…');
    expect(spinnerBeforeText(loading)).toBe(true);
  });

  it('without a provider: the bare "Loading…" text, unchanged', () => {
    render(wrap(<ProcessDetailView processId="abc" />, false));
    expect(screen.getByTestId('optio-detail-loading').innerHTML).toBe('Loading…');
  });
});

describe('IframeWidget — spinner slot', () => {
  it('loading: the provided Spinner before "Loading…", keyed to the process, size small; test id unchanged', () => {
    render(wrap(<IframeWidget {...iframeProps({ widgetData: undefined })} />));
    const loading = screen.getByTestId('optio-widget-loading');
    expect(within(loading).getByTestId('rec-spinner').getAttribute('data-key')).toBe('optio:process:abc:widget');
    // Exactly its own props: nothing injected by antd (className, style, percent).
    expect(propsOf('optio:process:abc:widget')).toStrictEqual({ spinnerKey: 'optio:process:abc:widget', size: 'small' });
    expect(loading.textContent).toBe('Loading…');
    expect(spinnerBeforeText(loading)).toBe(true);
  });

  it('loaded: no Spinner rendered', () => {
    render(wrap(<IframeWidget {...iframeProps({ widgetData: {} })} />));
    expect(screen.getByTestId('optio-widget-iframe')).toBeTruthy();
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });

  it('without a provider: the bare "Loading…" text, unchanged', () => {
    render(wrap(<IframeWidget {...iframeProps({ widgetData: undefined })} />, false));
    expect(screen.getByTestId('optio-widget-loading').innerHTML).toBe('Loading…');
  });
});

describe('SpinnerScope prefixes the optio-ui keys', () => {
  it('each site\'s key carries the scope chain', () => {
    render(wrap(
      <SpinnerScope name="outer">
        <SpinnerScope name="inner">
          <ProcessList processes={[]} loading />
          <LaunchControls process={resurrecting} onLaunch={vi.fn()} onResurrect={vi.fn()} />
          <ProcessDetailView processId="abc" />
          <IframeWidget {...iframeProps({ widgetData: undefined })} />
        </SpinnerScope>
      </SpinnerScope>,
    ));
    const keys = screen.getAllByTestId('rec-spinner').map((s) => s.getAttribute('data-key'));
    expect(keys.sort()).toEqual([
      'outer/inner/optio:process-list',
      'outer/inner/optio:process:9:resurrecting',
      'outer/inner/optio:process:abc:detail',
      'outer/inner/optio:process:abc:widget',
    ]);
  });
});

describe('re-rendering across the lent Spinner and the early returns is safe', () => {
  // The slot hooks are context reads, which React does not reject when called conditionally;
  // its dev build only logs the changed hook order. Assert that it never does.
  let consoleError: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { consoleError = vi.spyOn(console, 'error'); });
  afterEach(() => {
    const logged = consoleError.mock.calls.map((args) => args.map(String).join(' ')).join('\n');
    consoleError.mockRestore();
    expect(logged).not.toMatch(/order of Hooks|Rendered (more|fewer) hooks/);
  });

  it('LaunchControls: non-launchable (renders nothing) -> resurrecting, with and without a lent Spinner', () => {
    const running = { ...resurrecting, status: { state: 'running' } };
    const el = (p: any, withSpinner: boolean) => stable(
      <LaunchControls process={p} onLaunch={vi.fn()} onResurrect={vi.fn()} />, withSpinner,
    );
    const { rerender, container } = render(el(running, false));
    expect(screen.queryByRole('button')).toBeNull();
    rerender(el(resurrecting, true));
    expect(screen.getByTestId('rec-spinner').getAttribute('data-key')).toBe('optio:process:9:resurrecting');
    rerender(el(resurrecting, false));
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
    expect(container.querySelector('.anticon-loading')).not.toBeNull();
    rerender(el(running, true));
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('ProcessDetailView: no process selected -> loading, with and without a lent Spinner', () => {
    // The real useProcessStream holds state; without a hook here React would see no hooks on the
    // first render and have no order to compare later renders against.
    mockProcessStream.mockImplementation(() => {
      React.useRef(null);
      return { tree: null, logs: [], connected: false };
    });
    const { rerender } = render(stable(<ProcessDetailView processId={null} />, false));
    expect(screen.getByTestId('optio-detail-empty')).toBeTruthy();
    rerender(stable(<ProcessDetailView processId="abc" />, true));
    expect(screen.getByTestId('rec-spinner').getAttribute('data-key')).toBe('optio:process:abc:detail');
    rerender(stable(<ProcessDetailView processId="abc" />, false));
    expect(screen.getByTestId('optio-detail-loading').innerHTML).toBe('Loading…');
  });

  it('IframeWidget: loaded -> loading -> loaded, with and without a lent Spinner', () => {
    const { rerender } = render(stable(<IframeWidget {...iframeProps({ widgetData: {} })} />, false));
    expect(screen.getByTestId('optio-widget-iframe')).toBeTruthy();
    rerender(stable(<IframeWidget {...iframeProps({ widgetData: undefined })} />, true));
    expect(screen.getByTestId('rec-spinner').getAttribute('data-key')).toBe('optio:process:abc:widget');
    rerender(stable(<IframeWidget {...iframeProps({ widgetData: undefined })} />, false));
    expect(screen.getByTestId('optio-widget-loading').innerHTML).toBe('Loading…');
    rerender(stable(<IframeWidget {...iframeProps({ widgetData: {} })} />, true));
    expect(screen.getByTestId('optio-widget-iframe')).toBeTruthy();
  });

  it('ProcessList: loading toggled with and without a lent Spinner', () => {
    const { rerender, container } = render(stable(<ProcessList processes={[]} loading />, false));
    expect(container.querySelector('.ant-spin-dot-holder')).not.toBeNull();
    rerender(stable(<ProcessList processes={[]} loading />, true));
    expect(screen.getByTestId('rec-spinner').getAttribute('data-key')).toBe('optio:process-list');
    rerender(stable(<ProcessList processes={[]} loading={false} />, true));
    expect(screen.queryByTestId('rec-spinner')).toBeNull();
  });
});
