import { describe, expect, it } from "vitest";
import type { Job, Project } from "./api";
import { fmtDuration, jobSeconds, overall, runSteps } from "./progress";

const job = (o: Partial<Job>): Job => ({
  id: 1, kind: "repair", module: null, seq: 0, state: "queued", pct: null, message: null, error: null, attempts: 0, ...o,
});
const proj = (o: Partial<Project>): Project => ({
  id: "p", name: "a.wav", created: 0, state: "processing", sr: 48000, channels: 1, duration_s: 60, codec: null,
  lossy: false, has_video: false, streams: null, modules: ["declip", "glitch"], scan: null, result: null, error: null,
  size_bytes: 0, ...o,
});

describe("progress", () => {
  it("counts every step the same and extrapolates the remaining time", () => {
    const p = proj({
      now: 1100,
      jobs: [
        job({ kind: "repair", module: "declip", seq: 0, state: "done", started: 1000, finished: 1060 }),
        job({ kind: "repair", module: "glitch", seq: 1, state: "running", pct: 50, started: 1060 }),
      ],
    });
    const o = overall(p);
    expect(o.pct).toBeCloseTo(50);            // (1 + 0.5 + 0) / 3 steps
    expect(o.elapsed).toBeCloseTo(100);       // 60 s done + 40 s running
    expect(o.remaining).toBeCloseTo(100);
    expect(runSteps(p).map((s) => s.kind)).toEqual(["repair", "repair", "finalize"]);
  });

  it("with earlier runs on record, knows the time left before a step reports progress", () => {
    // 60 s file; declip took 0.5 s/s before (30 s), glitch 0.1 (6 s), finalize 0.04 (2.4 s)
    const rates = { "repair:declip": 0.5, "repair:glitch": 0.1, "finalize:": 0.04 };
    const p = proj({ now: 1010, rates, jobs: [job({ kind: "repair", module: "declip", seq: 0, state: "running", pct: 0, started: 1000 })] });
    const o = overall(p);
    expect(o.remaining).toBeCloseTo(20 + 6 + 2.4); // 10 s of 30 s spent on declip
    expect(o.pct).toBeCloseTo((10 / 38.4) * 100);
    // a step with no history falls back to the even split
    expect(overall(proj({ now: 1010, rates: { "repair:declip": 0.5 }, jobs: p.jobs })).remaining).toBeNull();
  });

  it("has no estimate before anything ran, and while scanning shows only the scan", () => {
    expect(overall(proj({ jobs: [] })).remaining).toBeNull();
    const s = proj({ state: "scanning", jobs: [job({ kind: "scan", state: "running", pct: 30, started: 10 })], now: 20 });
    expect(runSteps(s)).toHaveLength(1);
    expect(overall(s).pct).toBeCloseTo(30);
  });

  it("times a job to its finish or to now", () => {
    expect(jobSeconds(job({ started: 5, finished: 7.5 }), 100)).toBe(2.5);
    expect(jobSeconds(job({ started: 5 }), 9)).toBe(4);
    expect(jobSeconds(job({}), 9)).toBeNull();
  });

  it("formats durations", () => {
    expect(fmtDuration(12.34)).toBe("12.3 s");
    expect(fmtDuration(185)).toBe("3:05");
    expect(fmtDuration(3725)).toBe("1:02:05");
  });
});
