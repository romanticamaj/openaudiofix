import { gpuSummary, projectFixCount, stateGroup, TONE } from "./status";
import type { ModuleInfo, Project } from "./api";

const mod = (state: string, detail: Record<string, any> = {}): ModuleInfo =>
  ({ id: "denoise", name: "d", device: "gpu", state, step: null, detail });

test("stateGroup", () => {
  expect(stateGroup("done")).toBe("done");
  expect(stateGroup("scanning")).toBe("busy");
  expect(stateGroup("processing")).toBe("busy");
  expect(stateGroup("needs_stream")).toBe("waiting");
  expect(stateGroup("ready")).toBe("waiting");
  expect(stateGroup("failed")).toBe("failed");
  expect(stateGroup("cancelled")).toBe("failed");
  expect(stateGroup("weird")).toBe("failed");
  expect(TONE.busy).toBe("run");
});

test("gpuSummary", () => {
  expect(gpuSummary(mod("installed", { gpu: "RTX 5070 Ti", speed_x: 13.9 }))).toEqual(
    { tone: "ok", key: "gpu.card.ready", vars: { gpu: "RTX 5070 Ti", speed: 13.9 } });
  expect(gpuSummary(mod("downloading", { pct: 41.6 })).vars.pct).toBe("42%");
  expect(gpuSummary(mod("checking"))).toEqual({ tone: "run", key: "gpu.card.installing", vars: { pct: "" } });
  expect(gpuSummary(mod("verifying")).tone).toBe("run");
  expect(gpuSummary(mod("ready_to_download")).tone).toBe("wait");
  expect(gpuSummary(mod("not_installed")).tone).toBe("idle");
  expect(gpuSummary(undefined)).toEqual({ tone: "idle", key: "gpu.card.none", vars: {} });
  expect(gpuSummary(mod("blocked")).tone).toBe("fail");
  expect(gpuSummary(mod("failed")).key).toBe("gpu.card.unavailable");
});

test("projectFixCount", () => {
  const p = (result: any) => ({ result }) as Project;
  expect(projectFixCount(p({ counts: { glitch: 3, declip: 2 } }))).toBe(5);
  expect(projectFixCount(p(null))).toBe(0);
});
