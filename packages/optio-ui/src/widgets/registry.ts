import type { ComponentType } from 'react';

export interface WidgetProps {
  process: any;
  apiBaseUrl: string;
  widgetProxyUrl: string; // ends with '/' — trailing slash is load-bearing
  prefix: string;
  database?: string;
}

export type WidgetComponent = ComponentType<WidgetProps>;

const widgets = new Map<string, WidgetComponent>();

export function registerWidget(name: string, component: WidgetComponent): void {
  widgets.set(name, component);
}

export function getWidget(name: string): WidgetComponent | undefined {
  return widgets.get(name);
}

// Test-only reset. Not exported from package entry point.
export function _clearWidgetRegistry(): void {
  widgets.clear();
}
