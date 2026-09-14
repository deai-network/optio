import { configure } from '@testing-library/react';

// The global constraint asks tests to wait on conditions with a 60s hang
// ceiling rather than sleep-then-assert; testing-library's own waitFor/
// findBy* default to a 1s asyncUtilTimeout, which is too tight on this
// package's loaded, memory-constrained host (an antd dropdown portal can
// take over 1s to mount and flake the steering tests). Raise it package-wide.
configure({ asyncUtilTimeout: 60_000 });

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
