import { PREROLL, Transport, type MediaLike } from "./transport";

class FakeMedia implements MediaLike {
  currentTime = 0; paused = true; listeners: Record<string, (() => void)[]> = {};
  constructor(public url: string) {}
  play() { this.paused = false; (this.listeners.play ?? []).forEach((f) => f()); }
  pause() { this.paused = true; (this.listeners.pause ?? []).forEach((f) => f()); }
  addEventListener(t: string, f: () => void) { (this.listeners[t] ??= []).push(f); }
}

function make() {
  const tp = new Transport((u) => new FakeMedia(u));
  tp.dur = 100;
  tp.setSources([{ id: "original", url: "o" }, { id: "s0", url: "a" }, { id: "d_chain", url: "d" }], "s0");
  tp.setFixes([10, 10.1, 30, 50]);
  return tp;
}

test("switching keeps position and play state", () => {
  const tp = make();
  tp.seek(12.5); tp.toggle();
  tp.select("original");
  expect(tp.cur).toBe("original");
  expect(tp.t).toBe(12.5);
  expect(tp.playing).toBe(true);
  tp.destroy();
});

test("only one source plays at a time", () => {
  const tp = make();
  tp.toggle();
  tp.select("d_chain");
  expect(tp.playing).toBe(true);
  tp.select("s0");
  tp.toggle();
  expect(tp.playing).toBe(false);
  tp.destroy();
});

test("fixes within 0.25 s merge; next/prev jump with preroll", () => {
  const tp = make();
  expect(tp.fixCount).toBe(3);
  tp.next();
  expect(tp.t).toBeCloseTo(10 - PREROLL);
  tp.next();
  expect(tp.t).toBeCloseTo(30 - PREROLL);
  tp.prev();
  expect(tp.t).toBeCloseTo(10 - PREROLL);
  expect(tp.fixIndex()).toBe(1);
  tp.destroy();
});

test("seek clamps to the duration", () => {
  const tp = make();
  tp.seek(-5); expect(tp.t).toBe(0);
  tp.seek(500); expect(tp.t).toBe(100);
  tp.destroy();
});
