import type { ComponentType, ReactNode } from 'react';

export interface WidgetProps {
  process: any;
  apiBaseUrl: string;
  widgetProxyUrl: string; // ends with '/' — trailing slash is load-bearing
  prefix: string;
  database?: string;
  /** The host shows the process read-only (`ProcessDetailView readOnly`):
   *  the widget must not launch or resume it. Absent: not read-only. */
  readOnly?: boolean;
  /** For a conversation widget: leave out the runs a resume added where
   *  nothing happened (the agent woke up, was told it had been resumed,
   *  answered that it was ready, and was suspended again). Widgets without
   *  runs ignore it. Absent: every run shows. */
  hideEmptyRuns?: boolean;
  /** What the host shows while the process waits for its widget
   *  (`useProcessWidgetState`'s `placeholder`). A widget with a loading
   *  state of its own (e.g. the suspended conversation's fetch) shows it
   *  there. Absent: the widget's own loading indicator. */
  placeholder?: ReactNode;
  /** Given to a live widget only while it takes over from the suspended
   *  variant (a resume): it is mounted hidden, and shown once it calls this,
   *  i.e. once it shows the process (for the conversation: its backlog
   *  applied). Absent otherwise. See useProcessWidgetState. */
  onReady?: () => void;
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
