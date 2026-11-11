import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GpuModulePanel } from "./GpuModulePanel";

class FakeES {
  static all: FakeES[] = [];
  closed = false;
  onmessage: ((m: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeES.all.push(this); }
  close() { this.closed = true; }
  emit(state: string, updated: number, detail: Record<string, unknown> = {}, step: string | null = null) {
    act(() => this.onmessage?.({ data: JSON.stringify({ id: "denoise", state, step, detail, updated }) }));
  }
}

const ready = { gpu: "RTX 4070", vram: "12 GB", driver: "555", download_bytes: 7e9, free_bytes: 5e10, enough_space: true };

beforeEach(() => {
  FakeES.all = [];
  (globalThis as any).EventSource = FakeES;
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({}))) as any;
});

test("a stale terminal state replayed after the click does not end progress updates", async () => {
  render(<GpuModulePanel />);
  const first = FakeES.all[0];
  first.emit("ready_to_download", 100, ready, "confirm");
  expect(first.closed).toBe(true); // nothing pending: a terminal state ends the stream
  fireEvent.click(await screen.findByRole("button", { name: "Download and install" }));
  await waitFor(() => expect(FakeES.all.length).toBe(2));
  const es = FakeES.all[1];
  es.emit("ready_to_download", 100, ready, "confirm"); // replay of the state from before the click
  expect(es.closed).toBe(false);
  es.emit("downloading", 101, { pct: 40, done_bytes: 4e9, total_bytes: 1e10 }, "download");
  expect(screen.getByText(/Downloading… 40%/)).toBeTruthy();
  expect(es.closed).toBe(false);
  es.emit("installed", 105, { gpu: "RTX 4070", speed_x: 12 }, "done");
  expect(es.closed).toBe(true);
  expect(screen.getByText(/Ready · RTX 4070 · 12× real time/)).toBeTruthy();
  const post = (globalThis.fetch as any).mock.calls.find((c: any[]) => c[0].endsWith("/install"));
  expect(JSON.parse(post[1].body)).toEqual({ confirm: true });
});

test("a stale failure replayed after Check again is ignored until a newer state arrives", async () => {
  render(<GpuModulePanel />);
  FakeES.all[0].emit("blocked", 50, { code: "nvidia_missing" }, "precheck");
  fireEvent.click(await screen.findByRole("button", { name: "Check again" }));
  await waitFor(() => expect(FakeES.all.length).toBe(2));
  const es = FakeES.all[1];
  es.emit("blocked", 50, { code: "nvidia_missing" }, "precheck");
  expect(es.closed).toBe(false);
  es.emit("ready_to_download", 60, ready, "confirm");
  expect(es.closed).toBe(true);
});

test("an untouched not_installed module does not start the GPU check by itself", async () => {
  render(<GpuModulePanel />);
  FakeES.all[0].emit("not_installed", 0);
  expect(await screen.findByRole("button", { name: "Check GPU" })).toBeTruthy();
  expect((globalThis.fetch as any).mock.calls.some((c: any[]) => c[0].endsWith("/install"))).toBe(false);
});

test("inside a Docker image there is no Remove, and the image hint is shown when it has no GPU stack", async () => {
  render(<GpuModulePanel />);
  FakeES.all[0].emit("installed", 10, { gpu: "RTX 4070", speed_x: 12, managed: "image" }, "done");
  expect(await screen.findByRole("button", { name: "Test again" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  FakeES.all[0].emit("blocked", 11, { code: "install_disabled", managed: "image" }, "precheck");
  expect(screen.getByText(/docker-compose\.gpu\.yml/)).toBeTruthy();
});

test("the download shows the package and model phases", async () => {
  render(<GpuModulePanel />);
  const es = FakeES.all[0];
  es.emit("downloading", 10, { pct: 30, done_bytes: 1e9, total_bytes: 3.8e9, phase: "packages" }, "download");
  expect(screen.getByText(/Downloading… 30%/)).toBeTruthy();
  es.emit("downloading", 11, { pct: 94, phase: "installing" }, "download");
  expect(screen.getByText("Installing the GPU packages…")).toBeTruthy();
  es.emit("downloading", 12, { pct: 95, phase: "weights" }, "download");
  expect(screen.getByText("Downloading the MossFormer2 model…")).toBeTruthy();
});
