import { fireEvent, render } from "@testing-library/react";
import { overview, Timeline } from "./Timeline";
import { Transport } from "../transport";

test("renders under jsdom with many fixes on one canvas, controlled by view/onView", () => {
  globalThis.fetch = (() => Promise.resolve({ ok: true, json: () => Promise.resolve({ peaks: [] }) })) as unknown as typeof fetch;
  const fixes = Array.from({ length: 15298 }, (_, i) => ({
    module: i % 2 ? "glitch" : "declip", kind: "x", channel: 0, start: i * 100, end: i * 100 + 10, seconds: i / 441,
  }));
  const tp = new Transport(() => ({ currentTime: 0, paused: true, play() {}, pause() {}, addEventListener() {} }));
  const onView = vi.fn();
  const { container } = render(
    <Timeline projectId="p" src="s0" duration={60} sr={44100} fixes={fixes} transport={tp} view={{ t0: 0, t1: 60 }} onView={onView} />,
  );
  expect(container.querySelectorAll("canvas")).toHaveLength(1);
  expect(container.querySelectorAll("div").length).toBeLessThan(10);
  // The zoom-to-fit button moved to the workspace toolbar.
  expect(container.querySelectorAll("button")).toHaveLength(0);
  // A time ruler labels the visible span.
  const ticks = [...container.querySelectorAll(".ruler span")].map((s) => s.textContent);
  expect(ticks[0]).toBe("0:00.0");
  expect(ticks.at(-1)).toBe("1:00.0");
});

test("two wheel steps before a re-render both count (the view ref is updated at once)", () => {
  globalThis.fetch = (() => Promise.resolve({ ok: true, json: () => Promise.resolve({ peaks: [] }) })) as unknown as typeof fetch;
  const tp = new Transport(() => ({ currentTime: 0, paused: true, play() {}, pause() {}, addEventListener() {} }));
  const onView = vi.fn();
  const { container } = render(
    <Timeline projectId="p" src="s0" duration={60} sr={44100} fixes={[]} transport={tp} view={{ t0: 0, t1: 60 }} onView={onView} />,
  );
  const tl = container.querySelector(".timeline")!;
  fireEvent.wheel(tl, { deltaY: -100, clientX: 0 });
  fireEvent.wheel(tl, { deltaY: -100, clientX: 0 });
  expect(onView).toHaveBeenCalledTimes(2);
  expect(onView.mock.calls[0][0].t1).toBeCloseTo(48);
  expect(onView.mock.calls[1][0].t1).toBeCloseTo(38.4);
});

test("a click seeks to exactly the clicked time, inside a fix box or not, at any zoom", () => {
  globalThis.fetch = (() => Promise.resolve({ ok: true, json: () => Promise.resolve({ peaks: [] }) })) as unknown as typeof fetch;
  const tp = new Transport(() => ({ currentTime: 0, paused: true, play() {}, pause() {}, addEventListener() {} }));
  const seek = vi.spyOn(tp, "seek").mockImplementation(() => {});
  const fixes = [{ module: "speech", kind: "removed", channel: -1, start: 10 * 100, end: 20 * 100, seconds: 10 }];
  const show = (view: { t0: number; t1: number }) => {
    const { container, unmount } = render(
      <Timeline projectId="p" src="s0" duration={60} sr={100} fixes={fixes} transport={tp} view={view} onView={vi.fn()} />,
    );
    const tl = container.querySelector(".timeline")!;
    tl.getBoundingClientRect = () => ({ left: 100, top: 0, width: 800, height: 100, right: 900, bottom: 100, x: 100, y: 0, toJSON() {} });
    return { tl, unmount };
  };
  // jsdom has no PointerEvent, so fireEvent.pointer* would drop clientX; send mouse events of the pointer types.
  const click = (tl: Element, x: number) => {
    for (const type of ["pointerdown", "pointerup"]) fireEvent(tl, new MouseEvent(type, { bubbles: true, clientX: x }));
  };
  const a = show({ t0: 0, t1: 60 });
  click(a.tl, 100 + 160); // 12 s, inside the 10-20 s box: not its centre
  expect(seek.mock.lastCall![0]).toBeCloseTo(12);
  click(a.tl, 100 + 400); // 30 s, no box
  expect(seek.mock.lastCall![0]).toBeCloseTo(30);
  a.unmount();
  const b = show({ t0: 10, t1: 20 });
  click(b.tl, 100 + 200); // a quarter of the zoomed view
  expect(seek.mock.lastCall![0]).toBeCloseTo(12.5);
});

test("a new result gets its own overview: re-processing never shows the old waveform", async () => {
  const f = vi.fn(async () => new Response("[0, 0]"));
  globalThis.fetch = f as unknown as typeof fetch;
  await overview("rev-test", "s0", 1);
  await overview("rev-test", "s0", 1);
  expect(f).toHaveBeenCalledTimes(1);
  await overview("rev-test", "s0", 2);
  expect(f).toHaveBeenCalledTimes(2);
  expect(String(f.mock.calls.at(-1)!.at(0))).toContain("v=2");
});
