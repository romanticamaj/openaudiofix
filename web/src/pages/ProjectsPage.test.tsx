import { render, screen, fireEvent, waitFor, within, act, cleanup } from "@testing-library/react";
import * as apiMod from "../api";
import { ToastProvider } from "../components/ui/Toast";
import { ProjectsPage } from "./ProjectsPage";

vi.mock("../api", async (orig) => {
  const actual = await orig<typeof import("../api")>();
  return { ...actual, upload: vi.fn() };
});

const mk = (o: Record<string, unknown>) => ({
  sr: 48000, channels: 2, codec: "pcm_s16le", streams: null, modules: null, scan: null, error: null,
  size_bytes: 2048, created: 1760000000, duration_s: 65, ...o,
});
const listing = {
  projects: [
    mk({ id: "abc", name: "訪談.wav", state: "done", result: { counts: { glitch: 3, declip: 2 } } }),
    mk({ id: "run", name: "Song.mp3", state: "processing" }),
    mk({ id: "bad", name: "broken.wav", state: "failed" }),
    mk({ id: "can", name: "stopped.wav", state: "cancelled" }),
  ],
  total_bytes: 2048, free_bytes: 5e10,
};
const modules = { modules: [{ id: "denoise", name: "Denoise", device: "gpu", state: "installed", step: null, detail: { gpu: "RTX 4090", speed_x: 12 } }] };

const renderPage = () => render(<ToastProvider><ProjectsPage /></ToastProvider>);
const rows = () => screen.getAllByRole("row").slice(1);

beforeEach(() => {
  location.hash = "#/";
  vi.spyOn(apiMod.api, "projects").mockImplementation(async () => structuredClone(listing) as any);
  vi.spyOn(apiMod.api, "modules").mockImplementation(async () => modules as any);
  vi.spyOn(apiMod.api, "rename").mockResolvedValue({} as any);
  vi.spyOn(apiMod.api, "remove").mockResolvedValue({ ok: true });
  vi.mocked(apiMod.upload).mockReset();
  globalThis.fetch = vi.fn(async () => new Response("{}", { status: 404 })) as any;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("stat cards show the computed numbers", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  const stat = (label: string) => screen.getAllByText(label).map((e) => e.closest(".stat")).find(Boolean) as HTMLElement;
  expect(within(stat("Projects")).getByText("4")).toBeTruthy();
  expect(within(stat("Fixed spots")).getByText("5")).toBeTruthy();
  expect(within(stat("Processing")).getByText("1")).toBeTruthy();
  expect(await screen.findByText("GPU ready")).toBeTruthy();
});

test("the footer shows disk usage", async () => {
  renderPage();
  expect(await screen.findByText(/2.0 KB used by projects/)).toBeTruthy();
});

test("typing in search filters rows", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search projects" }), { target: { value: "SONG" } });
  expect(rows()).toHaveLength(1);
  expect(screen.getByText("Song.mp3")).toBeTruthy();
});

test("the Failed chip shows failed and cancelled projects only", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  const chip = screen.getByRole("button", { name: /Failed/ });
  expect(chip.textContent).toContain("2");
  fireEvent.click(chip);
  expect(chip.getAttribute("aria-pressed")).toBe("true");
  expect(rows().map((r) => r.textContent)).toEqual([expect.stringContaining("broken.wav"), expect.stringContaining("stopped.wav")]);
});

function openMenu(name: string) {
  const row = screen.getByText(name).closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: "Project actions" }));
}

test("Menu, Rename, Enter calls api.rename and reloads", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  openMenu("訪談.wav");
  fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
  const input = screen.getByRole("textbox") as HTMLInputElement;
  expect(input.value).toBe("訪談.wav");
  fireEvent.change(input, { target: { value: "new" } });
  const calls = vi.mocked(apiMod.api.projects).mock.calls.length;
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(apiMod.api.rename).toHaveBeenCalledWith("abc", "new"));
  await waitFor(() => expect(vi.mocked(apiMod.api.projects).mock.calls.length).toBeGreaterThan(calls));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("Escape cancels the rename dialog", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  openMenu("訪談.wav");
  fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(apiMod.api.rename).not.toHaveBeenCalled();
});

test("an api.rename rejection shows a toast", async () => {
  vi.mocked(apiMod.api.rename).mockRejectedValue(new apiMod.ApiError(500, "rename_failed", "rename blew up"));
  renderPage();
  await screen.findByText("訪談.wav");
  openMenu("訪談.wav");
  fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "new" } });
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  expect(await screen.findByText("rename blew up")).toBeTruthy();
});

test("Menu, Delete needs two clicks before api.remove", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  openMenu("訪談.wav");
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  expect(apiMod.api.remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete for good?" }));
  await waitFor(() => expect(apiMod.api.remove).toHaveBeenCalledWith("abc"));
  expect(screen.queryByRole("menu")).toBeNull();
});

test("clicking a row navigates; opening its menu does not", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  openMenu("訪談.wav");
  expect(location.hash).toBe("#/");
  fireEvent.click(screen.getByText("Song.mp3"));
  expect(location.hash).toBe("#/p/run");
});

test("Enter on a focused row navigates", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  const row = screen.getByText("broken.wav").closest("tr")!;
  expect(row.tabIndex).toBe(0);
  fireEvent.keyDown(row, { key: "Enter" });
  expect(location.hash).toBe("#/p/bad");
});

test("the name cell carries the full name as title", async () => {
  renderPage();
  expect((await screen.findByText("訪談.wav")).closest("[title]")!.getAttribute("title")).toBe("訪談.wav");
});

test("an empty list shows the drop prompt", async () => {
  vi.mocked(apiMod.api.projects).mockResolvedValue({ projects: [], total_bytes: 0, free_bytes: 1 });
  renderPage();
  expect(await screen.findByText("Drop an audio file here, or press Upload")).toBeTruthy();
});

test("a successful upload opens the new project", async () => {
  vi.mocked(apiMod.upload).mockResolvedValue({ id: "newid", name: "a.mp3" } as any);
  renderPage();
  await screen.findByText("訪談.wav");
  fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(["x"], "a.mp3")] } });
  await waitFor(() => expect(location.hash).toBe("#/p/newid"));
});

test("uploading shows a temporary first row with progress", async () => {
  let resolve!: (p: any) => void;
  vi.mocked(apiMod.upload).mockImplementation((_f, onProgress) => {
    onProgress(42);
    return new Promise((r) => { resolve = r; });
  });
  renderPage();
  await screen.findByText("訪談.wav");
  fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(["x"], "a.mp3")] } });
  expect(await screen.findByText("Uploading… 42%")).toBeTruthy();
  expect(rows()[0].textContent).toContain("a.mp3");
  await act(async () => { resolve({ id: "z" }); });
});

test("a rejected upload toasts the server error, stays on the list and reloads", async () => {
  vi.mocked(apiMod.upload).mockRejectedValue(new apiMod.ApiError(413, "too_long", "too long", "Trim it first."));
  renderPage();
  await screen.findByText("訪談.wav");
  const calls = vi.mocked(apiMod.api.projects).mock.calls.length;
  fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(["x"], "long.wav")] } });
  expect(await screen.findByText("This file is longer than 4 hours.")).toBeTruthy();
  expect(location.hash).toBe("#/");
  await waitFor(() => expect(vi.mocked(apiMod.api.projects).mock.calls.length).toBeGreaterThan(calls));
  expect(screen.getByText("訪談.wav")).toBeTruthy();
});

describe("page-wide drop", () => {
  const drag = (type: string, types: string[], files: File[] = []) => {
    const e = new Event(type, { bubbles: true, cancelable: true }) as any;
    e.dataTransfer = { types, files };
    act(() => { window.dispatchEvent(e); });
    return e as Event;
  };

  test("a non-file drag shows no overlay", async () => {
    renderPage();
    await screen.findByText("訪談.wav");
    drag("dragenter", ["text/plain"]);
    expect(screen.queryByText("Drop to upload")).toBeNull();
  });

  test("a file drag shows the overlay and drop uploads the first file", async () => {
    vi.mocked(apiMod.upload).mockResolvedValue({ id: "dropped" } as any);
    renderPage();
    await screen.findByText("訪談.wav");
    drag("dragenter", ["Files"]);
    expect(screen.getByText("Drop to upload")).toBeTruthy();
    const f = new File(["x"], "d.wav");
    drag("drop", ["Files"], [f, new File(["y"], "e.wav")]);
    expect(screen.queryByText("Drop to upload")).toBeNull();
    await waitFor(() => expect(apiMod.upload).toHaveBeenCalledTimes(1));
    expect(vi.mocked(apiMod.upload).mock.calls[0][0]).toBe(f);
    await waitFor(() => expect(location.hash).toBe("#/p/dropped"));
  });

  test("a depth counter keeps the overlay up across child enter/leave", async () => {
    renderPage();
    await screen.findByText("訪談.wav");
    drag("dragenter", ["Files"]);
    drag("dragenter", ["Files"]);
    drag("dragleave", ["Files"]);
    expect(screen.getByText("Drop to upload")).toBeTruthy();
    drag("dragleave", ["Files"]);
    expect(screen.queryByText("Drop to upload")).toBeNull();
  });
});

test("poll failures do not stack error toasts", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    renderPage();
    await screen.findByText("訪談.wav");
    const before = vi.mocked(apiMod.api.projects).mock.calls.length;
    vi.mocked(apiMod.api.projects).mockRejectedValue(new apiMod.ApiError(0, "net_down", "server down"));
    for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(vi.mocked(apiMod.api.projects).mock.calls.length - before).toBeGreaterThanOrEqual(3);
    expect(document.querySelectorAll(".toast.error").length).toBeLessThanOrEqual(1);
  } finally { vi.useRealTimers(); }
});

test("a failed first load toasts once and does not show the empty state", async () => {
  vi.mocked(apiMod.api.projects).mockRejectedValue(new apiMod.ApiError(0, "net_down", "server down"));
  renderPage();
  expect(await screen.findByText("server down")).toBeTruthy();
  expect(document.querySelectorAll(".toast.error")).toHaveLength(1);
  expect(screen.queryByText("Drop an audio file here, or press Upload")).toBeNull();
});

test("a zero-match search shows a no-match row", async () => {
  renderPage();
  await screen.findByText("訪談.wav");
  fireEvent.change(screen.getByRole("searchbox", { name: "Search projects" }), { target: { value: "zzz" } });
  expect(screen.getByText("No matching projects")).toBeTruthy();
});

test("a file dropped during an upload shows an info toast", async () => {
  vi.mocked(apiMod.upload).mockImplementation(() => new Promise(() => {}));
  renderPage();
  await screen.findByText("訪談.wav");
  fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(["x"], "a.mp3")] } });
  await screen.findByText(/Uploading/);
  const e = new Event("drop", { bubbles: true, cancelable: true }) as any;
  e.dataTransfer = { types: ["Files"], files: [new File(["y"], "b.wav")] };
  act(() => { window.dispatchEvent(e); });
  expect(await screen.findByText("An upload is already in progress")).toBeTruthy();
  expect(apiMod.upload).toHaveBeenCalledTimes(1);
});
