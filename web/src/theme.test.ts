import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, loadTheme, saveTheme } from "./theme";

describe("theme preference", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("defaults to dark with empty storage", () => {
    expect(loadTheme()).toBe("dark");
  });
  it("round-trips a saved value", () => {
    saveTheme("light");
    expect(loadTheme()).toBe("light");
  });
  it("falls back to dark on an invalid stored value", () => {
    localStorage.setItem("afix.theme", "purple");
    expect(loadTheme()).toBe("dark");
  });
  it("falls back to dark when getItem throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(loadTheme()).toBe("dark");
  });
  it("does not throw when setItem throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(() => saveTheme("light")).not.toThrow();
  });
  it("applyTheme sets data-theme on the root element", () => {
    applyTheme("system");
    expect(document.documentElement.dataset.theme).toBe("system");
  });
});
