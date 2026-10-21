// antd 6 observes element sizes (@rc-component/resize-observer); jsdom has no
// ResizeObserver, so stub it for components to mount.
if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}

// antd's Form lays its items out on its grid, which asks window.matchMedia for
// the breakpoints; jsdom has none, so no query matches.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener(): void {},
    removeListener(): void {},
    addEventListener(): void {},
    removeEventListener(): void {},
    dispatchEvent: () => false,
  }) as MediaQueryList;
}
