// web/src/progress.ts
import type { Job, Project } from "./api";

export type Step = { kind: "scan" | "repair" | "finalize"; module: string | null; job?: Job };

/** The steps of the current run: the scan while scanning, otherwise each module then finalize. */
export function runSteps(p: Project): Step[] {
  const jobs = p.jobs ?? [];
  if (p.state === "scanning") return [{ kind: "scan", module: null, job: jobs.filter((j) => j.kind === "scan").at(-1) }];
  const mods = p.modules ?? [];
  if (!mods.length) return [];
  const steps: Step[] = mods.map((m, k) => ({
    kind: "repair", module: m, job: jobs.filter((j) => j.kind === "repair" && j.seq === k).at(-1),
  }));
  steps.push({ kind: "finalize", module: null, job: jobs.filter((j) => j.kind === "finalize").at(-1) });
  return steps;
}

/** Seconds a job has run: to its finish, or to `now` (server clock) while it runs; null before it starts. */
export function jobSeconds(j: Job | undefined, now: number): number | null {
  if (!j?.started) return null;
  return Math.max(0, (j.finished ?? now) - j.started);
}

export type Overall = { pct: number; elapsed: number; remaining: number | null };

/** What a step took before, scaled to this file: seconds, or null with no history. */
function estimate(p: Project, s: Step): number | null {
  const r = p.rates?.[`${s.kind}:${s.module ?? ""}`];
  return r != null && p.duration_s ? r * p.duration_s : null;
}

/**
 * With earlier runs of every step on record, steps weigh what they took before and the time left is known
 * from the start (a GPU step reports progress only per channel). Otherwise every step counts the same and the
 * time left is extrapolated from the time so far.
 */
export function overall(p: Project, now = p.now ?? Date.now() / 1000): Overall {
  const steps = runSteps(p);
  if (!steps.length) return { pct: 0, elapsed: 0, remaining: null };
  let elapsed = 0;
  const fracs = steps.map((s) => {
    elapsed += jobSeconds(s.job, now) ?? 0;
    const st = s.job?.state;
    return st === "done" ? 1 : st === "running" ? Math.min(100, Math.max(0, s.job?.pct ?? 0)) / 100 : 0;
  });
  const ests = steps.map((s) => estimate(p, s));
  if (ests.every((e) => e != null && e > 0)) {
    let total = 0, left = 0;
    steps.forEach((s, i) => {
      const e = ests[i]!;
      total += e;
      if (fracs[i] >= 1) return;
      const ran = jobSeconds(s.job, now) ?? 0;
      // the larger of reported progress and time spent; never quite done while it still runs
      const f = Math.max(fracs[i], Math.min(0.99, ran / e));
      left += e * (1 - f);
    });
    const pct = Math.min(99.9, ((total - left) / total) * 100);
    return { pct, elapsed, remaining: left };
  }
  const pct = (fracs.reduce((a, b) => a + b, 0) / steps.length) * 100;
  const remaining = pct >= 3 && pct < 100 ? (elapsed * (100 - pct)) / pct : null;
  return { pct, elapsed, remaining };
}

/** A duration: "12.3 s" under a minute, then m:ss or h:mm:ss. */
export function fmtDuration(s: number): string {
  if (s < 60) return `${s.toFixed(1)} s`;
  const t = Math.round(s), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  const ss = String(sec).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export type StepTime = { module: string | null; secs: number };

/** Time each step of a finished run took, and the total. */
export function runTiming(p: Project): { steps: StepTime[]; total: number } {
  const steps = runSteps(p).flatMap((s) => {
    const secs = jobSeconds(s.job, p.now ?? 0);
    return secs == null ? [] : [{ module: s.module, secs }];
  });
  return { steps, total: steps.reduce((a, r) => a + r.secs, 0) };
}
