import { render, screen, fireEvent, waitFor, within, cleanup } from "@testing-library/react";
import * as apiMod from "../api";
import { ApiError, type Project } from "../api";
import { ToastProvider } from "../components/ui/Toast";
import { ProjectPage } from "./ProjectPage";

const scan = {
  glitch: { module: "glitch", detected: true, count: 3, summary: {} },
  declip: { module: "declip", detected: false, count: 0, summary: {} },
  denoise: { module: "denoise", detected: true, count: 0, summary: { noise_floor_dbfs: -52 } },
};
const mk = (o: Partial<Project> = {}): Project => ({
  id: "p1", name: "a.wav", created: 1760000000, state: "ready", sr: 48000, channels: 2, duration_s: 10,
  codec: "pcm_s16le", lossy: false, has_video: false, streams: null, modules: null, scan, result: null,
  error: null, size_bytes: 2048, jobs: [], ...o,
} as Project);
const doneResult = {
  preview_gain_db: 0, export_gain_db: -2.5, counts: {}, notes: {}, byte_exact_possible: true,
  sources: [{ id: "original" }, { id: "s0", module: "glitch" }], diffs: [],
};
const modules = { modules: [
  { id: "denoise", name: "Denoise", device: "gpu", state: "not_installed", step: null, detail: {} },
  { id: "declip", name: "Declip", device: "cpu", state: "installed", step: null, detail: {} },
  { id: "glitch", name: "Glitch", device: "cpu", state: "installed", step: null, detail: {} },
] };

let current: Project;
const renderPage = () => render(<ToastProvider><ProjectPage id="p1" /></ToastProvider>);
const bar = () => within(screen.getByRole("banner"));
const inBar = (name: string) => waitFor(() => bar().getByRole("button", { name }));
const insp = () => within(screen.getByRole("complementary"));
const sw = (name: RegExp) => screen.getByRole("switch", { name }) as HTMLInputElement;

beforeEach(() => {
  location.hash = "#/p/p1";
  current = mk();
  (globalThis as any).Audio = class { currentTime = 0; paused = true; play() { return Promise.resolve(); } pause() {} addEventListener() {} removeEventListener() {} };
  globalThis.fetch = vi.fn(async () => new Response("{}", { status: 404 })) as any;
  vi.spyOn(apiMod.api, "project").mockImplementation(async () => structuredClone(current));
  vi.spyOn(apiMod.api, "modules").mockImplementation(async () => structuredClone(modules) as any);
  vi.spyOn(apiMod.api, "fixes").mockResolvedValue({ fixes: [] });
  vi.spyOn(apiMod.api, "run").mockImplementation(async () => { current = mk({ state: "processing", modules: ["glitch"] }); return current; });
  vi.spyOn(apiMod.api, "cancel").mockImplementation(async () => current);
  vi.spyOn(apiMod.api, "retry").mockImplementation(async () => current);
  vi.spyOn(apiMod.api, "rename").mockImplementation(async (_id, name) => { current = { ...current, name }; return current; });
  vi.spyOn(apiMod.api, "remove").mockResolvedValue({ ok: true });
  vi.spyOn(apiMod.api, "clean").mockImplementation(async () => current);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("ready: detection summary, default toggles, GPU gated with an Install link, Start runs the defaults", async () => {
  renderPage();
  expect(await screen.findByText("Choose modules on the left, then Start repair.")).toBeTruthy();
  await waitFor(() => expect(sw(/Glitch repair/).checked).toBe(true));
  expect(sw(/Declip/).checked).toBe(false); // not detected
  expect(sw(/Background repair/).checked).toBe(false); // GPU, never on by default
  expect(sw(/Background repair/).disabled).toBe(true);
  expect(sw(/Glitch repair/).disabled).toBe(false);
  expect(screen.getByRole("link", { name: "Install" }).getAttribute("href")).toBe("#/modules");
  expect(screen.getAllByText(/3 dropout seams found/).length).toBeGreaterThan(0);
  // Output radios are read-only until the project is done.
  expect(insp().queryByRole("radio")).toBeNull(); // nothing to choose before the repair has run
  fireEvent.click(bar().getByRole("button", { name: "Start repair" }));
  await waitFor(() => expect(apiMod.api.run).toHaveBeenCalledWith("p1", ["glitch"], {}));
});

test("Start sends the chosen modules in the fixed order glitch, declip", async () => {
  renderPage();
  await waitFor(() => expect(sw(/Declip/).disabled).toBe(false));
  fireEvent.click(sw(/Declip/));
  fireEvent.click(bar().getByRole("button", { name: "Start repair" }));
  await waitFor(() => expect(apiMod.api.run).toHaveBeenCalledWith("p1", ["glitch", "declip"], {}));
});

test("speech settings: shown when speech is on, sent with the run, remembered, out-of-range values not sent", async () => {
  try { localStorage.clear(); } catch { /* none */ }
  const speech = { id: "speech", name: "Speech", device: "cpu", state: "installed", step: null, detail: {}, params: { properties: {
    sensitivity: { type: "string", enum: ["low", "mid", "high"], default: "mid" },
    pad_before_ms: { type: "integer", minimum: 0, maximum: 1000, default: 50 },
    pad_after_ms: { type: "integer", minimum: 0, maximum: 1000, default: 100 },
    min_gap_ms: { type: "integer", minimum: 0, maximum: 2000, default: 300 } } } };
  vi.mocked(apiMod.api.modules).mockImplementation(async () => structuredClone({ modules: [...modules.modules, speech] }) as any);
  renderPage();
  await waitFor(() => expect(sw(/speech only/i).disabled).toBe(false));
  expect(screen.queryByRole("group", { name: /settings/ })).toBeNull();
  fireEvent.click(sw(/speech only/i));
  const group = within(screen.getByRole("group", { name: /settings/ }));
  const before = group.getByRole("spinbutton", { name: /Keep before speech/ }) as HTMLInputElement;
  expect(before.value).toBe("50");
  fireEvent.change(before, { target: { value: "5000" } }); // over the maximum: not taken
  expect(before.getAttribute("aria-invalid")).toBe("true");
  fireEvent.blur(before);
  expect(before.value).toBe("50");
  fireEvent.change(before, { target: { value: "500" } });
  fireEvent.change(group.getByRole("combobox", { name: /Sensitivity/ }), { target: { value: "high" } });
  fireEvent.click(bar().getByRole("button", { name: "Start repair" }));
  await waitFor(() => expect(apiMod.api.run).toHaveBeenCalledWith("p1", ["glitch", "speech"],
    { speech: { sensitivity: "high", pad_before_ms: 500, pad_after_ms: 100, min_gap_ms: 300 } }));
  expect(JSON.parse(localStorage.getItem("afix.moduleSettings") ?? "{}").speech.pad_before_ms).toBe(500);
  try { localStorage.clear(); } catch { /* none */ }
});

test("Start is disabled with nothing chosen; the lossy warning and the nothing-found note show", async () => {
  current = mk({ codec: "mp3", lossy: true, scan: { ...scan, glitch: { ...scan.glitch, detected: false, count: 0 } } });
  renderPage();
  expect(await screen.findByText(/mp3, a lossy format/)).toBeTruthy();
  expect(screen.getByText("No problems were detected. You can still run any repair.")).toBeTruthy();
  await waitFor(() => expect(sw(/Glitch repair/).disabled).toBe(false));
  expect((bar().getByRole("button", { name: "Start repair" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Lossy format")).toBeTruthy();
});

test("done: Reprocess makes the toggles editable and asks before starting", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  renderPage();
  await screen.findByTestId("workspace-slot");
  await waitFor(() => expect(sw(/Glitch repair/).checked).toBe(true));
  expect(sw(/Glitch repair/).disabled).toBe(true); // read-only, shows what was used
  expect(sw(/Declip/).checked).toBe(false);
  fireEvent.click(bar().getByRole("button", { name: "Reprocess" }));
  expect(sw(/Glitch repair/).disabled).toBe(false);
  expect(screen.getByText("Choose modules on the left, then Start repair.")).toBeTruthy();
  fireEvent.click(bar().getByRole("button", { name: "Start repair" }));
  expect(screen.getByText("This replaces the current results. Start?")).toBeTruthy();
  fireEvent.click(within(document.querySelector(".popover") as HTMLElement).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByText("This replaces the current results. Start?")).toBeNull();
  expect(apiMod.api.run).not.toHaveBeenCalled();
  fireEvent.click(bar().getByRole("button", { name: "Start repair" }));
  fireEvent.click(screen.getByRole("button", { name: "Start" }));
  await waitFor(() => expect(apiMod.api.run).toHaveBeenCalledWith("p1", ["glitch"], {}));
});

test("done: Cancel in the top bar leaves reprocess mode", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  renderPage();
  fireEvent.click(await inBar("Reprocess"));
  fireEvent.click(bar().getByRole("button", { name: "Cancel" }));
  expect(bar().getByRole("button", { name: "Reprocess" })).toBeTruthy();
  expect(sw(/Glitch repair/).disabled).toBe(true);
});

test("processing: the top-bar Cancel calls api.cancel", async () => {
  current = mk({ state: "processing", modules: ["glitch"] });
  renderPage();
  fireEvent.click(await inBar("Cancel"));
  await waitFor(() => expect(apiMod.api.cancel).toHaveBeenCalledWith("p1"));
});

test("scanning shows the scan card", async () => {
  current = mk({ state: "scanning", scan: null });
  renderPage();
  expect(await screen.findByText("Scanning for problems…")).toBeTruthy();
  expect(bar().getByRole("button", { name: "Cancel" })).toBeTruthy();
});

test("failed: Try again calls api.retry", async () => {
  current = mk({ state: "failed", modules: ["glitch"], error: { code: "internal_error", message: "boom" } });
  renderPage();
  fireEvent.click(await inBar("Try again"));
  await waitFor(() => expect(apiMod.api.retry).toHaveBeenCalledWith("p1"));
});

test("failed: the progress card has no Retry of its own, only the top bar's Try again", async () => {
  current = mk({ state: "failed", modules: ["glitch"], error: { code: "internal_error", message: "boom" } });
  renderPage();
  await inBar("Try again");
  expect(screen.getByText("Something went wrong.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry from this step" })).toBeNull();
  expect(screen.queryAllByRole("button", { name: "Cancel" })).toHaveLength(0);
});

test("processing: only one Cancel, in the top bar", async () => {
  current = mk({ state: "processing", modules: ["glitch"] });
  renderPage();
  await inBar("Cancel");
  expect(screen.getAllByRole("button", { name: "Cancel" })).toHaveLength(1);
});

const twoStreams = [{ index: 0, codec: "aac", sr: 48000, channels: 2 }, { index: 1, codec: "ac3", sr: 48000, channels: 6 }];

test("needs_stream: Use this track goes through act (refresh + sidebar notice)", async () => {
  current = mk({ state: "needs_stream", streams: twoStreams, scan: null });
  const choose = vi.spyOn(apiMod.api, "chooseStream").mockImplementation(async () => { current = mk({ state: "scanning", scan: null }); return current; });
  const changed = vi.fn();
  addEventListener(apiMod.PROJECTS_CHANGED, changed);
  renderPage();
  fireEvent.click(await screen.findByRole("radio", { name: /Track 2/ }));
  fireEvent.click(screen.getByRole("button", { name: "Use this track" }));
  await waitFor(() => expect(choose).toHaveBeenCalledWith("p1", 1));
  await waitFor(() => expect(changed).toHaveBeenCalled());
  removeEventListener(apiMod.PROJECTS_CHANGED, changed);
  expect(await screen.findByText("Scanning for problems…")).toBeTruthy();
});

test("needs_stream: a choose error goes to a toast", async () => {
  current = mk({ state: "needs_stream", streams: twoStreams, scan: null });
  vi.spyOn(apiMod.api, "chooseStream").mockRejectedValueOnce(new ApiError(409, "x_test", "stream refused"));
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Use this track" }));
  const toast = await screen.findByRole("alert");
  expect(toast.textContent).toContain("stream refused");
  expect(toast.closest(".toasts")).toBeTruthy();
});

test("a 404 shows not-found with a link back", async () => {
  vi.mocked(apiMod.api.project).mockRejectedValue(new ApiError(404, "project_not_found", ""));
  renderPage();
  expect(await screen.findByText("Project not found")).toBeTruthy();
  expect(screen.getAllByRole("link").some((a) => a.getAttribute("href") === "#/" && a.closest(".notfound"))).toBe(true);
});

test("a done project whose later load 404s shows not-found", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  renderPage();
  await screen.findByTestId("workspace-slot");
  vi.mocked(apiMod.api.project).mockRejectedValue(new ApiError(404, "project_not_found", ""));
  fireEvent.click(bar().getByRole("button", { name: "Project actions" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Clean intermediate files" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Remove intermediate files?" }));
  expect(await screen.findByText("Project not found")).toBeTruthy();
  expect(screen.queryByTestId("workspace-slot")).toBeNull();
});

test("a processing project whose poll 404s shows not-found and stops polling", async () => {
  current = mk({ state: "processing", modules: ["glitch"] });
  renderPage();
  await inBar("Cancel");
  vi.mocked(apiMod.api.project).mockRejectedValue(new ApiError(404, "project_not_found", ""));
  expect(await screen.findByText("Project not found", {}, { timeout: 3000 })).toBeTruthy();
  const n = vi.mocked(apiMod.api.project).mock.calls.length;
  await new Promise((r) => setTimeout(r, 1300));
  expect(vi.mocked(apiMod.api.project).mock.calls.length).toBe(n);
}, 10000);

test("a failed poll keeps the page and clears the error on the next success", async () => {
  current = mk({ state: "processing", modules: ["glitch"] });
  renderPage();
  await inBar("Cancel");
  vi.mocked(apiMod.api.project).mockRejectedValueOnce(new ApiError(500, "internal_error", "boom"));
  expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toBeTruthy();
  expect(bar().getByText("a.wav")).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull(), { timeout: 3000 });
}, 10000);

test("rename: click the name, type, Enter saves; a rejection restores the old name and toasts", async () => {
  renderPage();
  fireEvent.click(await inBar("a.wav"));
  const input = bar().getByRole("textbox", { name: "Project name" }) as HTMLInputElement;
  expect(document.activeElement).toBe(input);
  fireEvent.change(input, { target: { value: " 新 " } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(apiMod.api.rename).toHaveBeenCalledWith("p1", "新"));
  expect(await inBar("新")).toBeTruthy();

  vi.mocked(apiMod.api.rename).mockRejectedValueOnce(new ApiError(409, "x_test", "nope"));
  fireEvent.click(bar().getByRole("button", { name: "新" }));
  const again = bar().getByRole("textbox", { name: "Project name" });
  fireEvent.change(again, { target: { value: "其他" } });
  fireEvent.keyDown(again, { key: "Enter" });
  expect(await screen.findByText("nope")).toBeTruthy();
  expect(bar().getByRole("button", { name: "新" })).toBeTruthy();
});

test("rename: Esc cancels without saving", async () => {
  renderPage();
  fireEvent.click(await inBar("a.wav"));
  const input = bar().getByRole("textbox", { name: "Project name" });
  fireEvent.change(input, { target: { value: "x" } });
  fireEvent.keyDown(input, { key: "Escape" });
  expect(bar().getByRole("button", { name: "a.wav" })).toBeTruthy();
  expect(apiMod.api.rename).not.toHaveBeenCalled();
});

test("menu Rename opens the inline editor", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  renderPage();
  fireEvent.click(await inBar("Project actions"));
  fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
  expect(document.activeElement).toBe(bar().getByRole("textbox", { name: "Project name" }));
});

test("menu Delete, clicked twice, removes the project and goes home", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  renderPage();
  fireEvent.click(await inBar("Project actions"));
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete project" }));
  expect(apiMod.api.remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete for good?" }));
  await waitFor(() => expect(apiMod.api.remove).toHaveBeenCalledWith("p1"));
  await waitFor(() => expect(location.hash).toBe("#/"));
});

test("menu Clean is disabled once the project is cleaned", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: { ...doneResult, cleaned: true } });
  renderPage();
  fireEvent.click(await inBar("Project actions"));
  expect((screen.getByRole("menuitem", { name: "Clean intermediate files" }) as HTMLButtonElement).disabled).toBe(true);
});

test("an action error goes to a toast", async () => {
  vi.mocked(apiMod.api.run).mockRejectedValueOnce(new ApiError(409, "x_test", "cannot start"));
  renderPage();
  await waitFor(() => expect(sw(/Glitch repair/).checked).toBe(true));
  fireEvent.click(bar().getByRole("button", { name: "Start repair" }));
  expect(await screen.findByText("cannot start")).toBeTruthy();
});

const exportRes = { files: [{ kind: "repaired", name: "a_afix.wav", url: "/x/a_afix.wav" }], gain_db: -2.5, subtype: "PCM_16", byte_exact: true,
  zip_url: "/x/a.zip", can_open_folder: false, folder: null };
const pop = () => within(document.querySelector(".export-modal") as HTMLElement);

test("Space on a top-bar button does not toggle playback", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  (globalThis as any).Audio = class {
    currentTime = 0; paused = true;
    play() { this.paused = false; return Promise.resolve(); } pause() { this.paused = true; }
    addEventListener() {} removeEventListener() {}
  };
  renderPage();
  await screen.findByTestId("fix-counter");
  const reprocess = bar().getByRole("button", { name: "Reprocess" });
  reprocess.focus();
  expect(fireEvent.keyDown(reprocess, { key: " " })).toBe(true);
  expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
  fireEvent.keyDown(document.body, { key: " " });
  expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
});

test("(Review Focus 1) typing 1 2 into the rename field neither switches sources nor plays", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  renderPage();
  await screen.findByTestId("fix-counter");
  expect(screen.getByRole("button", { name: "Repaired" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(bar().getByRole("button", { name: "a.wav" }));
  const input = bar().getByRole("textbox", { name: "Project name" }) as HTMLInputElement;
  for (const key of ["1", " ", "2"]) expect(fireEvent.keyDown(input, { key })).toBe(true); // not default-prevented
  fireEvent.change(input, { target: { value: "1 2" } });
  expect(input.value).toContain("1 2");
  expect(screen.getByRole("button", { name: "Repaired" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Original" }).getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
});

test("export window: opens from the top bar, float exports with float=true, shows links, syncs the Inspector, Esc closes", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  const exp = vi.spyOn(apiMod.api, "exportFiles").mockResolvedValue(exportRes as any);
  renderPage();
  await screen.findByTestId("fix-counter");
  const exportBtn = bar().getByRole("button", { name: "Export" });
  expect(document.querySelector(".export-modal")).toBeNull();
  fireEvent.click(exportBtn);
  // The peaks went over full scale: keeping the level is what is offered first.
  expect((pop().getByRole("radio", { name: /Keep the level and waveform/ }) as HTMLInputElement).checked).toBe(true);
  expect(pop().getByRole("radio", { name: /lowered by 2.5 dB/ })).toBeTruthy();
  expect((insp().getByRole("radio", { name: /Keep the level and waveform/ }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(pop().getByRole("button", { name: "Export" }));
  await waitFor(() => expect(exp).toHaveBeenCalledWith("p1", true));
  const link = await pop().findByRole("link", { name: "a_afix.wav" });
  expect(link.getAttribute("href")).toBe("/x/a_afix.wav");
  // Switching the format in the Inspector clears stale links.
  fireEvent.click(insp().getByRole("radio", { name: /lowered by 2.5 dB/ }));
  expect((pop().getByRole("radio", { name: /lowered by 2.5 dB/ }) as HTMLInputElement).checked).toBe(true);
  expect(pop().queryByRole("link")).toBeNull();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(document.querySelector(".export-modal")).toBeNull();
  // It opens again, and a click on the backdrop closes it.
  fireEvent.click(exportBtn);
  fireEvent.mouseDown(document.querySelector(".export-backdrop")!);
  expect(document.querySelector(".export-modal")).toBeNull();
});

test("export window: a zip download link, and open-folder only when the server is on this computer", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  const exp = vi.spyOn(apiMod.api, "exportFiles").mockResolvedValue(exportRes as any);
  const open = vi.spyOn(apiMod.api, "openExports").mockResolvedValue({ folder: "C:/d/exports" });
  renderPage();
  await screen.findByTestId("fix-counter");
  fireEvent.click(bar().getByRole("button", { name: "Export" }));
  fireEvent.click(pop().getByRole("button", { name: "Export" }));
  const zip = await pop().findByRole("link", { name: "Download all (zip)" });
  expect(zip.getAttribute("href")).toBe("/x/a.zip");
  expect(pop().queryByRole("button", { name: "Open export folder" })).toBeNull();
  exp.mockResolvedValue({ ...exportRes, can_open_folder: true, folder: "C:/d/exports" } as any);
  fireEvent.click(pop().getByRole("button", { name: "Export" }));
  fireEvent.click(await pop().findByRole("button", { name: "Open export folder" }));
  await waitFor(() => expect(open).toHaveBeenCalledWith("p1"));
  expect(pop().getByText(/C:\/d\/exports/)).toBeTruthy();
});

test("an export error goes to a toast", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  vi.spyOn(apiMod.api, "exportFiles").mockRejectedValue(new ApiError(500, "x_test", "disk full"));
  renderPage();
  await screen.findByTestId("fix-counter");
  fireEvent.click(bar().getByRole("button", { name: "Export" }));
  fireEvent.click(pop().getByRole("button", { name: "Export" }));
  expect(await screen.findByText("disk full")).toBeTruthy();
  expect(document.querySelector(".toast.error")).toBeTruthy();
});

test("done: three columns with the fix list; the narrow tabs switch panels", async () => {
  current = mk({ state: "done", modules: ["glitch"], result: doneResult });
  vi.mocked(apiMod.api.fixes).mockResolvedValue({ fixes: [
    { module: "glitch", kind: "seam", channel: -1, start: 48000 * 2, end: 48000 * 2 + 100, seconds: 2 },
    { module: "glitch", kind: "seam", channel: -1, start: 48000 * 6, end: 48000 * 6 + 100, seconds: 6 },
  ] });
  renderPage();
  await screen.findByText("Fix 0 / 2");
  expect(document.querySelector(".project-grid")!.classList.contains("with-list")).toBe(true);
  expect(screen.getByRole("heading", { name: "Fixes 2 / 2" })).toBeTruthy();
  const tabs = within(screen.getByRole("tablist"));
  const wave = tabs.getByRole("tab", { name: "Waveform" }), list = tabs.getByRole("tab", { name: "Fixes" });
  expect(wave.getAttribute("aria-selected")).toBe("true");
  expect(document.querySelector(".fix-col")!.classList.contains("is-hidden")).toBe(true);
  expect(document.querySelector(".project-centre")!.classList.contains("is-hidden")).toBe(false);
  fireEvent.click(list);
  expect(list.getAttribute("aria-selected")).toBe("true");
  expect(document.querySelector(".fix-col")!.classList.contains("is-hidden")).toBe(false);
  expect(document.querySelector(".project-centre")!.classList.contains("is-hidden")).toBe(true);
  // Clicking a row selects it and seeks; the counter follows.
  fireEvent.click(screen.getByRole("button", { name: /0:06\.0/ }));
  expect(screen.getByRole("button", { name: /0:06\.0/ }).getAttribute("aria-current")).toBe("true");
  expect(screen.getByTestId("fix-counter").textContent).toBe("Fix 2 / 2");
  fireEvent.keyDown(document, { key: "ArrowLeft" });
  expect(screen.getByRole("button", { name: /0:02\.0/ }).getAttribute("aria-current")).toBe("true");
});

test("not done: no fix list column and no narrow tabs", async () => {
  renderPage();
  await screen.findByText("Choose modules on the left, then Start repair.");
  expect(document.querySelector(".project-grid")!.classList.contains("with-list")).toBe(false);
  expect(screen.queryByRole("tablist")).toBeNull();
});
