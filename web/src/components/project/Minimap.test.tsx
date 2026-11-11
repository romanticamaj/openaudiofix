import { cleanup, fireEvent, render } from "@testing-library/react";
import { Minimap } from "./Minimap";

// jsdom has no PointerEvent, so fireEvent.pointer* would drop clientX; a MouseEvent-based stand-in keeps it.
if (!("PointerEvent" in window)) {
  (window as any).PointerEvent = class extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 0; }
  };
}

beforeEach(() => {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify([]))) as any;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { left: 0, top: 0, width: 500, height: 40, right: 500, bottom: 40, x: 0, y: 0, toJSON() {} } as DOMRect);
});
afterEach(() => vi.restoreAllMocks());

test("the window sits at the view; dragging it 50px of 500px pans by a tenth of the file", () => {
  const onView = vi.fn();
  const { container } = render(<Minimap projectId="p" src="s0" duration={100} view={{ t0: 0, t1: 10 }} onView={onView} />);
  expect(container.querySelectorAll("canvas")).toHaveLength(1);
  const win = container.querySelector(".mini-win") as HTMLElement;
  expect(win.style.left).toBe("0%");
  expect(win.style.width).toBe("10%");
  fireEvent.pointerDown(win, { clientX: 20, pointerId: 1 });
  fireEvent.pointerMove(win, { clientX: 70, pointerId: 1 });
  fireEvent.pointerUp(win, { clientX: 70, pointerId: 1 });
  const v = onView.mock.calls.at(-1)![0];
  expect(v.t0).toBeCloseTo(10);
  expect(v.t1).toBeCloseTo(20);
});

test("a click outside the window centres the view there", () => {
  const onView = vi.fn();
  const { container } = render(<Minimap projectId="p" src="s0" duration={100} view={{ t0: 0, t1: 10 }} onView={onView} />);
  const box = container.querySelector(".minimap") as HTMLElement;
  fireEvent.pointerDown(box, { clientX: 250, pointerId: 1 });
  fireEvent.pointerUp(box, { clientX: 250, pointerId: 1 });
  const v = onView.mock.calls.at(-1)![0];
  expect(v.t0).toBeCloseTo(45);
  expect(v.t1).toBeCloseTo(55);
});

test("a click moves the playhead to the clicked time even where the window cannot centre on it", () => {
  const onView = vi.fn(), onSeek = vi.fn();
  // the whole file is in view: the window cannot move, the playhead still goes where the click was
  const { container } = render(
    <Minimap projectId="p" src="s0" duration={100} view={{ t0: 0, t1: 100 }} onView={onView} onSeek={onSeek} />);
  const win = container.querySelector(".mini-win") as HTMLElement;
  fireEvent.pointerDown(win, { clientX: 100, pointerId: 1 });
  fireEvent.pointerUp(win, { clientX: 100, pointerId: 1 });
  expect(onSeek.mock.lastCall![0]).toBeCloseTo(20);
  cleanup();
  // near the end of the file the window stops at the end
  const r = render(<Minimap projectId="p" src="s0" duration={100} view={{ t0: 0, t1: 10 }} onView={onView} onSeek={onSeek} />);
  fireEvent.pointerDown(r.container.querySelector(".minimap")!, { clientX: 495, pointerId: 1 });
  expect(onSeek.mock.lastCall![0]).toBeCloseTo(99);
  expect(onView.mock.lastCall![0].t1).toBeCloseTo(100);
});

test("clicking or dragging the window moves the playhead", () => {
  const onView = vi.fn(), onSeek = vi.fn();
  const { container } = render(
    <Minimap projectId="p" src="s0" duration={100} view={{ t0: 0, t1: 10 }} onView={onView} onSeek={onSeek} />);
  const win = container.querySelector(".mini-win") as HTMLElement;
  const box = container.querySelector(".minimap") as HTMLElement;
  // A click on the window itself (no drag) centres it on the click: 40px of 500 = 8 s.
  fireEvent.pointerDown(win, { clientX: 40, pointerId: 1 });
  fireEvent.pointerUp(win, { clientX: 40, pointerId: 1 });
  expect(onView.mock.calls.at(-1)![0].t0).toBeCloseTo(3);
  expect(onSeek).toHaveBeenLastCalledWith(8);
  // A drag pans and keeps the playhead at the centre.
  fireEvent.pointerDown(win, { clientX: 20, pointerId: 1 });
  fireEvent.pointerMove(win, { clientX: 70, pointerId: 1 });
  fireEvent.pointerUp(win, { clientX: 70, pointerId: 1 });
  expect(onSeek.mock.calls.at(-1)![0]).toBeCloseTo(15);
  // A click outside the window.
  fireEvent.pointerDown(box, { clientX: 250, pointerId: 1 });
  fireEvent.pointerUp(box, { clientX: 250, pointerId: 1 });
  expect(onSeek.mock.calls.at(-1)![0]).toBeCloseTo(50);
});
