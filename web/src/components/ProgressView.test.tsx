import { render, screen } from "@testing-library/react";
import { ProgressView } from "./ProgressView";
import type { Project } from "../api";

test("shows each step and the failure inline; retry lives in the top bar", () => {
  const project = {
    id: "p1", state: "failed", modules: ["glitch", "declip"], sr: 48000, channels: 1, duration_s: 10, codec: "pcm_s24le",
    error: { code: "out_of_memory", message: "x", hint: "Close other programs or process a shorter file.", detail: "Traceback…" },
    jobs: [
      { id: 1, kind: "scan", module: null, seq: 0, state: "done", pct: null, message: null, error: null, attempts: 1 },
      { id: 2, kind: "repair", module: "glitch", seq: 0, state: "done", pct: null, message: null, error: null, attempts: 1 },
      { id: 3, kind: "repair", module: "declip", seq: 1, state: "failed", pct: 40, message: null,
        error: { code: "out_of_memory", message: "x" }, attempts: 1 },
    ],
  } as unknown as Project;
  render(<ProgressView project={project} />);
  expect(screen.getByText("Glitch repair")).toBeTruthy();
  expect(screen.getByText("The server ran out of memory.")).toBeTruthy();
  expect(screen.getByText(/Close other programs/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry from this step" })).toBeNull();
  expect(screen.getByRole("button", { name: "Copy error report" })).toBeTruthy();
  expect(screen.getByText("Traceback…")).toBeTruthy();
});

test("running step shows percent and no cancel button (the top bar has it)", () => {
  const project = {
    id: "p1", state: "processing", modules: ["declip"],
    jobs: [{ id: 3, kind: "repair", module: "declip", seq: 0, state: "running", pct: 40, message: null, error: null, attempts: 1 }],
  } as unknown as Project;
  render(<ProgressView project={project} />);
  expect(screen.getByText(/Working 40%/)).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});
