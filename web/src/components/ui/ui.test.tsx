import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import { Badge } from "./Badge";
import { Menu } from "./Menu";
import { Sparkline } from "./Sparkline";
import { ToastProvider, useToast } from "./Toast";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("Badge", () => {
  it("maps processing to run tone", () => {
    const { container } = render(<Badge state="processing" />);
    const el = container.querySelector("span.badge")!;
    expect(el.className).toBe("badge run");
    expect(el.textContent).toContain("Repairing");
  });
});

describe("Menu", () => {
  const setup = (onSelect = vi.fn(), spy = vi.fn()) => {
    render(
      <div onClick={spy}>
        <Menu label="More" items={[{ label: "Rename", onSelect }, { label: "Delete", onSelect: () => {}, danger: true }]} />
      </div>,
    );
    return { onSelect, spy };
  };
  it("opens on click and shows items", () => {
    setup();
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getAllByRole("menuitem")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "More" }).getAttribute("aria-expanded")).toBe("true");
  });
  it("closes on Escape", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });
  it("closes on outside pointerdown", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
  it("selecting calls onSelect once and closes", () => {
    const { onSelect } = setup();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });
  it("does not bubble clicks to the parent", () => {
    const { spy } = setup();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    expect(spy).not.toHaveBeenCalled();
  });
  describe("placement", () => {
    let restore: () => void = () => {};
    const stub = (triggerTop: number, triggerBottom: number, listH: number, innerH: number) => {
      const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
        return { top: triggerTop, bottom: triggerBottom, left: 200, right: 300, width: 100, height: triggerBottom - triggerTop, x: 200, y: triggerTop, toJSON() {} } as DOMRect;
      });
      const desc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
      Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
        configurable: true,
        get(this: HTMLElement) { return this.classList.contains("menu-list") ? listH : 0; },
      });
      const ih = window.innerHeight;
      Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: innerH });
      restore = () => {
        rect.mockRestore();
        if (desc) Object.defineProperty(HTMLElement.prototype, "offsetHeight", desc);
        Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: ih });
      };
    };
    afterEach(() => restore());
    it("opens downward when there is room", () => {
      stub(100, 120, 80, 800);
      render(<Menu label="More" items={[{ label: "Rename", onSelect: () => {} }]} />);
      fireEvent.click(screen.getByRole("button", { name: "More" }));
      const list = screen.getByRole("menu");
      expect(list.style.top).toBe("124px");
      expect(list.style.bottom).toBe("");
    });
    it("flips upward near the viewport bottom", () => {
      stub(740, 760, 80, 800);
      render(<Menu label="More" items={[{ label: "Rename", onSelect: () => {} }]} />);
      fireEvent.click(screen.getByRole("button", { name: "More" }));
      const list = screen.getByRole("menu");
      // list bottom sits at trigger top - 4 = 736, i.e. 800 - 736 = 64px from the viewport bottom
      expect(list.style.bottom).toBe("64px");
      expect(list.style.top).toBe("auto");
    });
  });
  it("keepOpen items call onSelect but leave the menu open", () => {
    const onSelect = vi.fn();
    render(<Menu label="More" items={[{ label: "Delete", onSelect, keepOpen: true }]} />);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("menu")).toBeTruthy();
  });
});

function Fire({ fn }: { fn: (t: ReturnType<typeof useToast>) => void }) {
  const toast = useToast();
  return <button onClick={() => fn(toast)}>go</button>;
}

describe("Toast", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  it("info auto-dismisses after 5s", () => {
    render(<ToastProvider><Fire fn={(t) => t.info("hello x")} /></ToastProvider>);
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByText("hello x")).toBeTruthy();
    act(() => { vi.advanceTimersByTime(5001); });
    expect(screen.queryByText("hello x")).toBeNull();
  });
  it("error persists until closed and uses the translated code", () => {
    render(<ToastProvider><Fire fn={(t) => t.error(new ApiError(500, "disk_full", "m"))} /></ToastProvider>);
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByText("Not enough free disk space.")).toBeTruthy();
    act(() => { vi.advanceTimersByTime(10000); });
    expect(screen.getByText("Not enough free disk space.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Not enough free disk space.")).toBeNull();
  });
  it("falls back to message for unknown codes and plain errors", () => {
    render(<ToastProvider><Fire fn={(t) => { t.error(new ApiError(500, "zzz_unknown", "raw msg")); t.error("plain"); }} /></ToastProvider>);
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByText("raw msg")).toBeTruthy();
    expect(screen.getByText("plain")).toBeTruthy();
  });
  it("error toasts are alerts; info toasts are status", () => {
    render(<ToastProvider><Fire fn={(t) => { t.error("bad thing"); t.info("fyi"); }} /></ToastProvider>);
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByRole("alert").textContent).toContain("bad thing");
    expect(screen.getByRole("status").textContent).toContain("fyi");
  });
});

describe("Sparkline", () => {
  it("is flat when fetch 404s", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    const { container } = render(<Sparkline projectId="p1" enabled />);
    await waitFor(() => expect(container.querySelector("canvas")!.getAttribute("data-state")).toBe("flat"));
  });
  it("is wave when peaks load", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([-0.5, 0.5, -0.4, 0.6, -0.1, 0.2]), { status: 200 })));
    const { container } = render(<Sparkline projectId="p2" enabled />);
    await waitFor(() => expect(container.querySelector("canvas")!.getAttribute("data-state")).toBe("wave"));
  });
  it("does not fetch when disabled", () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const { container } = render(<Sparkline projectId="p3" enabled={false} />);
    expect(f).not.toHaveBeenCalled();
    expect(container.querySelector("canvas")!.getAttribute("data-state")).toBe("flat");
  });
});

describe("Menu positioning", () => {
  it("renders the list fixed-positioned so table wrappers cannot clip it", () => {
    render(<Menu label="More" items={[{ label: "Rename", onSelect: () => {} }]} />);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect((screen.getByRole("menu") as HTMLElement).style.position).toBe("fixed");
  });
});
