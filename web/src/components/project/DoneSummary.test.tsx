import { fireEvent, render, screen } from "@testing-library/react";
import type { Job, Project } from "../../api";
import { DoneSummary } from "./DoneSummary";

const job = (o: Partial<Job>): Job => ({
  id: 1, kind: "repair", module: null, seq: 0, state: "done", pct: 100, message: null, error: null, attempts: 0, ...o,
});
const proj: Project = {
  id: "p1", name: "a.wav", created: 0, state: "done", sr: 48000, channels: 2, duration_s: 120, codec: null,
  lossy: false, has_video: false, streams: null, modules: ["denoise", "speech"], scan: null, result: null, error: null,
  size_bytes: 0, now: 2000,
  jobs: [
    job({ module: "denoise", seq: 0, started: 1000, finished: 1030 }),
    job({ module: "speech", seq: 1, started: 1030, finished: 1036 }),
    job({ kind: "finalize", started: 1036, finished: 1040 }),
  ],
};

beforeEach(() => { try { localStorage.clear(); } catch { /* none */ } });

test("shows the total time, speed against real time and each step, and stays closed once closed", () => {
  const { unmount } = render(<DoneSummary project={proj} />);
  const box = screen.getByRole("region");
  expect(box.textContent).toContain("40.0 s");   // 30 + 6 + 4
  expect(box.textContent).toContain("3.0");      // 120 s of audio in 40 s
  expect(box.textContent).toContain("30.0 s");
  expect(box.textContent).toContain("6.0 s");
  fireEvent.click(screen.getByRole("button"));
  expect(screen.queryByRole("region")).toBeNull();
  unmount();
  render(<DoneSummary project={proj} />);
  expect(screen.queryByRole("region")).toBeNull();
});
