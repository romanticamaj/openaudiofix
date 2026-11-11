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
  fail() { act(() => this.onerror?.()); }
}

const ready = { gpu: "RTX 4070", vram: "12 GB", driver: "555", download_bytes: 7e9, free_bytes: 5e10, enough_space: true };
const calls = () => (globalThis.fetch as any).mock.calls as any[][];
const posted = (suffix: string, method?: string) =>
  calls().filter((c) => c[0].endsWith(suffix) && (!method || c[1]?.method === method));

beforeEach(() => {
  FakeES.all = [];
  (globalThis as any).EventSource = FakeES;
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({}))) as any;
});

test("after Remove the not_installed state offers a Check GPU button instead of a stuck progress message", async () => {
  render(<GpuModulePanel />);
  FakeES.all[0].emit("installed", 10, { gpu: "RTX 4070", speed_x: 9 }, "done");
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(FakeES.all.length).toBe(2));
  const es = FakeES.all[1];
  es.emit("installed", 10, {}, "done"); // stale replay
  es.emit("not_installed", 20);
  expect(es.closed).toBe(true);
  expect(screen.queryByText(/Checking for an NVIDIA GPU/)).toBeNull();
  expect(posted("/install")).toHaveLength(0); // no automatic check after the user removed it
  fireEvent.click(screen.getByRole("button", { name: "Check GPU" }));
  await waitFor(() => expect(posted("/install", "POST")).toHaveLength(1));
});

test("unmount closes the stream and cancels a pending reconnect", async () => {
  const { unmount } = render(<GpuModulePanel />);
  const es = FakeES.all[0];
  es.emit("downloading", 5, { pct: 10 }, "download");
  es.fail(); // schedules a reconnect
  unmount();
  expect(es.closed).toBe(true);
  await new Promise((r) => setTimeout(r, 800));
  expect(FakeES.all).toHaveLength(1);
});

test("a dropped stream is reopened while the last state was not terminal", async () => {
  render(<GpuModulePanel />);
  FakeES.all[0].emit("downloading", 5, { pct: 10 }, "download");
  FakeES.all[0].fail();
  await waitFor(() => expect(FakeES.all.length).toBe(2), { timeout: 2000 });
  FakeES.all[1].emit("installed", 9, { gpu: "G", speed_x: 3 }, "done");
  expect(FakeES.all[1].closed).toBe(true);
  FakeES.all[1].fail();
  await new Promise((r) => setTimeout(r, 800));
  expect(FakeES.all).toHaveLength(2); // terminal and idle: no more reconnects
});

test("409 busy shows the error, keeps following, and a newer state clears the error", async () => {
  globalThis.fetch = vi.fn(async () =>
    new Response(JSON.stringify({ code: "busy", message: "busy" }), { status: 409 })) as any;
  render(<GpuModulePanel />);
  FakeES.all[0].emit("installed", 10, { gpu: "G", speed_x: 3 }, "done");
  fireEvent.click(screen.getByRole("button", { name: "Test again" }));
  expect(await screen.findByText("The GPU module is busy; try again in a moment.")).toBeTruthy();
  await waitFor(() => expect(FakeES.all.length).toBe(2));
  const es = FakeES.all[1];
  es.emit("verifying", 11, {}, "verify");
  expect(es.closed).toBe(false);
  expect(screen.queryByText(/is busy/)).toBeNull();
});

test("download is disabled when there is not enough disk space", () => {
  render(<GpuModulePanel />);
  FakeES.all[0].emit("ready_to_download", 3, { ...ready, enough_space: false }, "confirm");
  expect((screen.getByRole("button", { name: "Download and install" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Not enough free disk space for the download.")).toBeTruthy();
});

test("packages_missing at verify re-runs the GPU check, not the self-test", async () => {
  render(<GpuModulePanel />);
  FakeES.all[0].emit("failed", 4, { code: "packages_missing", error: "x" }, "verify");
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() => expect(posted("/install", "POST")).toHaveLength(1));
  expect(JSON.parse(posted("/install", "POST")[0][1].body)).toEqual({ confirm: false });
  expect(posted("/verify")).toHaveLength(0);
});

test("a failed removal offers removal again", async () => {
  render(<GpuModulePanel />);
  FakeES.all[0].emit("failed", 4, { code: "uninstall_failed", error: "x" }, "remove");
  expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(calls().some((c) => c[1]?.method === "DELETE")).toBe(true));
});
