// jsdom does not implement ResizeObserver, which the conversation widget uses
// for auto-scroll. Provide a no-op so component tests can mount the widget;
// auto-scroll behavior itself is exercised manually in the dashboard.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}
