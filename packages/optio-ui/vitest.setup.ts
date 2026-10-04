// antd 6 observes element sizes (@rc-component/resize-observer); jsdom has no
// ResizeObserver, so stub it for components to mount.
if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}
