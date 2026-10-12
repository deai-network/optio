import type { ComponentType } from 'react';

export interface WidgetProps {
  process: any;
  apiBaseUrl: string;
  widgetProxyUrl: string; // ends with '/' — trailing slash is load-bearing
  prefix: string;
  database?: string;
  /** The host shows the process read-only (`ProcessDetailView readOnly`):
   *  the widget must not launch or resume it. Absent: not read-only. */
  readOnly?: boolean;
}

export type WidgetComponent = ComponentType<WidgetProps>;

export interface RegisterWidgetOptions {
  /** Rendered instead of the live component when the process is suspended
   *  (`isSuspended`) rather than widget-live. Receives the same `WidgetProps`.
   *  Omitting it (or omitting `options` entirely) leaves any variant already
   *  registered for this name untouched — a plain `registerWidget(name, c)`
   *  call keeps working exactly as before. */
  suspended?: WidgetComponent;
}

const widgets = new Map<string, WidgetComponent>();
const suspendedWidgets = new Map<string, WidgetComponent>();

export function registerWidget(
  name: string,
  component: WidgetComponent,
  options?: RegisterWidgetOptions,
): void {
  widgets.set(name, component);
  if (options?.suspended) {
    suspendedWidgets.set(name, options.suspended);
  }
}

export function getWidget(name: string): WidgetComponent | undefined {
  return widgets.get(name);
}

export function getSuspendedWidget(name: string): WidgetComponent | undefined {
  return suspendedWidgets.get(name);
}

// Test-only reset. Not exported from package entry point.
export function _clearWidgetRegistry(): void {
  widgets.clear();
  suspendedWidgets.clear();
}
