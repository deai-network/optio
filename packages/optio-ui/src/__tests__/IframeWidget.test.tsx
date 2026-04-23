import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import React from 'react';
import { IframeWidget } from '../widgets/IframeWidget.js';

function makeProps(overrides: Partial<any> = {}) {
  return {
    process: {
      _id: 'abc',
      processId: 'p',
      name: 'P',
      status: { state: 'running' },
      progress: { percent: null },
      ...overrides,
    },
    apiBaseUrl: 'http://localhost:3000',
    widgetProxyUrl: 'http://localhost:3000/api/widget/abc/',
    prefix: 'optio',
  };
}

describe('IframeWidget', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('renders a loading placeholder when widgetData is absent', () => {
    render(<IframeWidget {...makeProps({ widgetData: undefined })} />);
    expect(screen.queryByTestId('optio-widget-iframe')).toBeNull();
    expect(screen.getByTestId('optio-widget-loading')).toBeTruthy();
  });

  it('mounts iframe when widgetData is present', () => {
    render(<IframeWidget {...makeProps({ widgetData: {} })} />);
    const iframe = screen.getByTestId('optio-widget-iframe') as HTMLIFrameElement;
    expect(iframe.src).toContain('/api/widget/abc/');
  });

  it('writes localStorageOverrides before mount and clears on unmount', () => {
    const props = makeProps({
      widgetData: { localStorageOverrides: { 'my.key': 'v1' } },
    });
    const { unmount } = render(<IframeWidget {...props} />);
    expect(localStorage.getItem('my.key')).toBe('v1');
    unmount();
    expect(localStorage.getItem('my.key')).toBeNull();
  });

  it('honors iframeSrc override', () => {
    const props = makeProps({
      widgetData: { iframeSrc: 'http://other.example/' },
    });
    render(<IframeWidget {...props} />);
    const iframe = screen.getByTestId('optio-widget-iframe') as HTMLIFrameElement;
    expect(iframe.src).toBe('http://other.example/');
  });

  it('honors sandbox and allow overrides', () => {
    const props = makeProps({
      widgetData: { sandbox: 'allow-scripts', allow: 'clipboard-read' },
    });
    render(<IframeWidget {...props} />);
    const iframe = screen.getByTestId('optio-widget-iframe') as HTMLIFrameElement;
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.getAttribute('allow')).toBe('clipboard-read');
  });

  it('shows session-ended banner on terminal state but keeps iframe mounted', () => {
    const props = makeProps({
      status: { state: 'done' },
      widgetData: {},
    });
    render(<IframeWidget {...props} />);
    expect(screen.getByTestId('optio-widget-iframe')).toBeTruthy();
    expect(screen.getByTestId('optio-widget-session-ended')).toBeTruthy();
  });

  it('substitutes {widgetProxyUrl} in localStorageOverrides values', () => {
    const props = makeProps({
      widgetData: {
        localStorageOverrides: {
          'opencode.settings.dat:defaultServerUrl': '{widgetProxyUrl}',
          'static.key': 'static-value',
        },
      },
    });
    render(<IframeWidget {...props} />);
    expect(
      localStorage.getItem('opencode.settings.dat:defaultServerUrl'),
    ).toBe('http://localhost:3000/api/widget/abc/');
    expect(localStorage.getItem('static.key')).toBe('static-value');
  });

  it('substitutes {widgetProxyUrl} in iframeSrc', () => {
    const props = makeProps({
      widgetData: { iframeSrc: '{widgetProxyUrl}%2Ftmp%2Fxyz/session/' },
    });
    render(<IframeWidget {...props} />);
    const iframe = screen.getByTestId('optio-widget-iframe') as HTMLIFrameElement;
    expect(iframe.src).toBe(
      'http://localhost:3000/api/widget/abc/%2Ftmp%2Fxyz/session/',
    );
  });
});
