// web/src/status.ts
import type { ModuleInfo, Project } from "./api";

export type Group = "done" | "busy" | "waiting" | "failed";

export function stateGroup(state: string): Group {
  switch (state) {
    case "done": return "done";
    case "scanning": case "processing": return "busy";
    case "needs_stream": case "ready": return "waiting";
    default: return "failed";
  }
}

export const TONE: Record<Group, "ok" | "run" | "wait" | "fail"> = { done: "ok", busy: "run", waiting: "wait", failed: "fail" };

export type GpuSummary = { tone: "ok" | "run" | "wait" | "idle" | "fail"; key: string; vars: Record<string, string | number> };

export function gpuSummary(m: ModuleInfo | undefined): GpuSummary {
  if (!m) return { tone: "idle", key: "gpu.card.none", vars: {} };
  const d = m.detail ?? {};
  switch (m.state) {
    case "installed": return { tone: "ok", key: "gpu.card.ready", vars: { gpu: d.gpu ?? "", speed: d.speed_x ?? "" } };
    case "checking": case "verifying": return { tone: "run", key: "gpu.card.installing", vars: { pct: "" } };
    case "downloading": return { tone: "run", key: "gpu.card.installing", vars: { pct: `${Math.round(d.pct ?? 0)}%` } };
    case "ready_to_download": return { tone: "wait", key: "gpu.card.confirm", vars: {} };
    case "blocked": case "failed": return { tone: "fail", key: "gpu.card.unavailable", vars: {} };
    default: return { tone: "idle", key: "gpu.card.none", vars: {} };
  }
}

export function projectFixCount(p: Project): number {
  return Object.values(p.result?.counts ?? {}).reduce((a, b) => a + b, 0);
}
