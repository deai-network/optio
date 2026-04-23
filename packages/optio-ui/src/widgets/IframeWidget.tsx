import { useEffect, useState } from 'react';
import type { WidgetProps } from './registry.js';
import { registerWidget } from './registry.js';

interface IframeWidgetData {
  localStorageOverrides?: Record<string, string>;
  iframeSrc?: string;
  sandbox?: string;
  allow?: string;
  title?: string;
}

const TERMINAL_STATES = new Set(['done', 'failed', 'cancelled']);

export function IframeWidget(props: WidgetProps) {
  const widgetData = (props.process.widgetData ?? undefined) as IframeWidgetData | undefined;
  const state: string | undefined = props.process.status?.state;
  const isTerminal = state !== undefined && TERMINAL_STATES.has(state);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  useEffect(() => {
    if (!widgetData?.localStorageOverrides) return;
    const keys = Object.keys(widgetData.localStorageOverrides);
    for (const k of keys) {
      const raw = widgetData.localStorageOverrides[k];
      const resolved = raw.replace(/\{widgetProxyUrl\}/g, props.widgetProxyUrl);
      localStorage.setItem(k, resolved);
    }
    return () => {
      for (const k of keys) localStorage.removeItem(k);
    };
  }, [widgetData?.localStorageOverrides, props.widgetProxyUrl]);

  if (!widgetData) {
    return <div data-testid="optio-widget-loading">Loading…</div>;
  }

  const rawSrc = widgetData.iframeSrc ?? props.widgetProxyUrl;
  const src = rawSrc.replace(/\{widgetProxyUrl\}/g, props.widgetProxyUrl);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <iframe
        data-testid="optio-widget-iframe"
        src={src}
        title={widgetData.title ?? props.process.name}
        sandbox={widgetData.sandbox}
        allow={widgetData.allow}
        style={{ width: '100%', height: '100%', border: 'none' }}
      />
      {isTerminal && !bannerDismissed && (
        <div
          data-testid="optio-widget-session-ended"
          style={{
            position: 'absolute', top: 0, left: 0, right: 0,
            padding: 8, background: '#fffbe6', borderBottom: '1px solid #ffe58f',
          }}
        >
          Session ended.
          <button onClick={() => setBannerDismissed(true)} style={{ marginLeft: 8 }}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

registerWidget('iframe', IframeWidget);
