import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { FixRow } from "../../api";
import { FixList } from "./FixList";

const SR = 48000;
const row = (module: string, seconds: number, len = 48): FixRow =>
  ({ module, kind: "x", channel: -1, start: Math.round(seconds * SR), end: Math.round(seconds * SR) + len, seconds });

const FIXES = [row("glitch", 5, 100), row("declip", 9, 6), row("declip", 12, 96)];
const rowButtons = () => screen.getAllByRole("button", { name: /\d:\d\d\.\d|Whole file/ });

test("header, chips and rows; a row click picks its seconds; the selected row is marked", () => {
  const onPick = vi.fn();
  const { rerender } = render(<FixList fixes={FIXES} selected={null} onPick={onPick} sr={SR} duration={20} />);
  expect(screen.getByRole("heading", { name: "Fixes 3 / 3" })).toBeTruthy();
  const rows = rowButtons();
  expect(rows).toHaveLength(3);
  expect(rows[0].textContent).toMatch(/0:05\.0/);
  expect(rows[0].textContent).toMatch(/Glitch repair/);
  expect(rows[0].textContent).toMatch(/2\.1 ms/);
  fireEvent.click(rows[1]);
  expect(onPick).toHaveBeenCalledWith(9, FIXES[1]);
  rerender(<FixList fixes={FIXES} selected={FIXES[1]} onPick={onPick} sr={SR} duration={20} />);
  const again = rowButtons();
  expect(again[1].getAttribute("aria-current")).toBe("true");
  expect(again[0].getAttribute("aria-current")).toBeNull();
});

test("the declip chip filters to declip rows and the header counts the filtered rows", () => {
  render(<FixList fixes={FIXES} selected={null} onPick={() => {}} sr={SR} duration={20} />);
  const chips = within(screen.getByRole("group"));
  expect(chips.getAllByRole("button").map((b) => b.textContent)).toEqual(["All 3", "Glitch repair 1", "Declip 2"]);
  fireEvent.click(chips.getByRole("button", { name: /Declip/ }));
  expect(chips.getByRole("button", { name: /Declip/ }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("heading", { name: "Fixes 2 / 3" })).toBeTruthy();
  const rows = rowButtons();
  expect(rows).toHaveLength(2);
  expect(rows.every((r) => /Declip/.test(r.textContent ?? ""))).toBe(true);
});

test("a whole-file fix shows as one Whole file row and picks 0", () => {
  const onPick = vi.fn();
  const whole = { module: "denoise", kind: "denoise", channel: -1, start: 0, end: SR * 20, seconds: 3 };
  render(<FixList fixes={[whole, FIXES[0]]} selected={null} onPick={onPick} sr={SR} duration={20} />);
  const w = screen.getByRole("button", { name: /Whole file/ });
  fireEvent.click(w);
  expect(onPick).toHaveBeenCalledWith(0, whole);
});

test("(Review Focus 4) 5,000 fixes render as a virtual window", () => {
  const many = Array.from({ length: 5000 }, (_, i) => row(i % 3 ? "declip" : "glitch", i * 0.5));
  render(<FixList fixes={many} selected={null} onPick={() => {}} sr={SR} duration={3000} />);
  expect(screen.getByRole("heading", { name: "Fixes 5000 / 5000" })).toBeTruthy();
  const rows = rowButtons();
  expect(rows.length).toBeGreaterThan(0);
  expect(rows.length).toBeLessThan(100);
  const spacer = document.querySelector(".fx-spacer") as HTMLElement;
  expect(spacer.style.height).toBe(`${5000 * 32}px`);
});

describe("selection scrolling", () => {
  // A controllable ResizeObserver and a settable clientHeight stand in for layout (jsdom has none).
  let observers: (() => void)[] = [];
  let height = 0;
  const realRO = globalThis.ResizeObserver;
  beforeEach(() => {
    observers = [];
    height = 0;
    globalThis.ResizeObserver = class {
      cb: () => void;
      constructor(cb: () => void) { this.cb = cb; observers.push(cb); }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get() { return this.classList?.contains("fx-body") ? height : 0; } });
  });
  afterEach(() => {
    globalThis.ResizeObserver = realRO;
    delete (HTMLElement.prototype as any).clientHeight;
  });
  const resize = (h: number) => act(() => { height = h; observers.forEach((f) => f()); });

  const many = Array.from({ length: 3000 }, (_, i) => row("declip", i));
  const target = /41:40\.0/; // many[2500]

  test("selecting a far row in virtual mode scrolls it into the window", () => {
    const { rerender } = render(<FixList fixes={many} selected={null} onPick={() => {}} sr={SR} duration={4000} />);
    resize(640);
    rerender(<FixList fixes={many} selected={many[2500]} onPick={() => {}} sr={SR} duration={4000} />);
    const body = document.querySelector(".fx-body") as HTMLElement;
    expect(body.scrollTop).toBe(2501 * 32 - 640);
    expect(screen.getByRole("button", { name: target }).getAttribute("aria-current")).toBe("true");
  });

  test("a selection made while the list is hidden is scrolled to when the list is shown (virtual)", () => {
    const { rerender } = render(<FixList fixes={many} selected={null} onPick={() => {}} sr={SR} duration={4000} />);
    // Hidden tab panel: clientHeight 0. Selecting must not pretend to have scrolled.
    rerender(<FixList fixes={many} selected={many[2500]} onPick={() => {}} sr={SR} duration={4000} />);
    const body = document.querySelector(".fx-body") as HTMLElement;
    expect(body.scrollTop).toBe(0);
    expect(screen.queryByRole("button", { name: target })).toBeNull();
    // The panel is shown: the selection is applied and the row is in the rendered window.
    resize(640);
    expect(body.scrollTop).toBe(2501 * 32 - 640);
    expect(screen.getByRole("button", { name: target }).getAttribute("aria-current")).toBe("true");
  });

  test("a selection made while the list is hidden is scrolled to when the list is shown (plain list)", () => {
    const spy = vi.fn();
    (HTMLElement.prototype as any).scrollIntoView = spy;
    try {
      const { rerender } = render(<FixList fixes={FIXES} selected={null} onPick={() => {}} sr={SR} duration={20} />);
      rerender(<FixList fixes={FIXES} selected={FIXES[2]} onPick={() => {}} sr={SR} duration={20} />);
      expect(spy).not.toHaveBeenCalled();
      resize(300);
      expect(spy).toHaveBeenCalledWith({ block: "nearest" });
      expect((spy.mock.contexts[0] as HTMLElement).textContent).toMatch(/0:12\.0/);
    } finally {
      delete (HTMLElement.prototype as any).scrollIntoView;
    }
  });
});
