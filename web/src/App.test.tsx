import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  location.hash = "#/";
  globalThis.fetch = vi.fn(async (u: string) => {
    const url = String(u);
    const body = url.endsWith("/api/health") ? { ok: true, checks: [] }
      : url.endsWith("/api/modules") ? { modules: [] }
      : { projects: [], total_bytes: 0, free_bytes: 0 };
    return new Response(JSON.stringify(body));
  }) as any;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("Escape closes the open drawer", async () => {
  const { container } = render(<App />);
  const sidebar = container.querySelector(".sidebar")!;
  fireEvent.click(screen.getAllByRole("button", { name: "Open navigation" })[0]);
  expect(sidebar.classList.contains("open")).toBe(true);
  expect(screen.getByTestId("nav-backdrop")).toBeTruthy();
  act(() => { fireEvent.keyDown(window, { key: "Escape" }); });
  expect(sidebar.classList.contains("open")).toBe(false);
  expect(screen.queryByTestId("nav-backdrop")).toBeNull();
});
