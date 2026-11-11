import { act, renderHook, waitFor } from "@testing-library/react";
import * as apiMod from "../../api";
import type { Project } from "../../api";
import { useModuleChoice } from "./useModuleChoice";

const scan = {
  glitch: { module: "glitch", detected: false, count: 0, summary: {} },
  declip: { module: "declip", detected: true, count: 2561, summary: {} },
  denoise: { module: "denoise", detected: true, count: 0, summary: { noise_floor_dbfs: -52 } },
};
const proj = (o: Partial<Project> = {}) => ({ id: "p1", state: "ready", scan, modules: null, ...o } as unknown as Project);
const mods = (gpu = "not_installed") => ({ modules: [
  { id: "denoise", name: "Denoise", device: "gpu", state: gpu, step: null, detail: {} },
  { id: "declip", name: "Declip", device: "cpu", state: "installed", step: null, detail: {} },
  { id: "glitch", name: "Glitch", device: "cpu", state: "installed", step: null, detail: {} },
] });

afterEach(() => vi.restoreAllMocks());

test("sorts glitch, declip, denoise and defaults on only for detected CPU modules", async () => {
  vi.spyOn(apiMod.api, "modules").mockResolvedValue(mods("installed") as any);
  const { result } = renderHook(() => useModuleChoice(proj(), true));
  await waitFor(() => expect(result.current.mods).toHaveLength(3));
  expect(result.current.mods.map((m) => m.id)).toEqual(["glitch", "declip", "denoise"]);
  expect(result.current.on).toEqual({ glitch: false, declip: true, denoise: false }); // GPU: never by default
  expect(result.current.chosen).toEqual(["declip"]);
  act(() => result.current.toggle("denoise"));
  act(() => result.current.toggle("glitch"));
  expect(result.current.chosen).toEqual(["glitch", "declip", "denoise"]); // fixed order, not click order
});

test("a module that is not installed never counts as chosen", async () => {
  vi.spyOn(apiMod.api, "modules").mockResolvedValue(mods() as any);
  const { result } = renderHook(() => useModuleChoice(proj(), true));
  await waitFor(() => expect(result.current.mods).toHaveLength(3));
  act(() => result.current.toggle("denoise"));
  expect(result.current.on.denoise).toBe(true);
  expect(result.current.chosen).toEqual(["declip"]);
});

test("read-only reflects the modules actually used, and toggling does nothing", async () => {
  vi.spyOn(apiMod.api, "modules").mockResolvedValue(mods("installed") as any);
  const { result } = renderHook(() => useModuleChoice(proj({ state: "done", modules: ["glitch", "denoise"] }), false));
  await waitFor(() => expect(result.current.mods).toHaveLength(3));
  expect(result.current.on).toEqual({ glitch: true, declip: false, denoise: true });
  act(() => result.current.toggle("declip"));
  expect(result.current.on.declip).toBe(false);
});

test("defaults follow a scan that finishes after the modules loaded", async () => {
  vi.spyOn(apiMod.api, "modules").mockResolvedValue(mods() as any);
  const { result, rerender } = renderHook(({ p }) => useModuleChoice(p, p.state === "ready"), {
    initialProps: { p: proj({ state: "scanning", scan: null }) },
  });
  await waitFor(() => expect(result.current.mods).toHaveLength(3));
  expect(result.current.chosen).toEqual([]);
  rerender({ p: proj() });
  expect(result.current.chosen).toEqual(["declip"]);
});
