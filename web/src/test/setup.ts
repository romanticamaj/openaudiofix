// jsdom has no canvas: give every canvas a no-op 2D context so components that draw can render.
const noop = () => undefined;
const ctx = new Proxy({ canvas: null, measureText: () => ({ width: 0 }) } as Record<string, unknown>, {
  get: (t, k: string) => (k in t ? t[k] : noop),
  set: (t, k: string, v) => { t[k] = v; return true; },
});

HTMLCanvasElement.prototype.getContext = function getContext() {
  return ctx as unknown as CanvasRenderingContext2D;
} as unknown as HTMLCanvasElement["getContext"];

globalThis.requestAnimationFrame ??= ((f: FrameRequestCallback) => setTimeout(() => f(0), 16) as unknown as number);
globalThis.cancelAnimationFrame ??= ((h: number) => clearTimeout(h));
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;
