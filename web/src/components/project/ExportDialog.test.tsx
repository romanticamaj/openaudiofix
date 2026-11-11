import { createRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import * as apiMod from "../../api";
import type { Project } from "../../api";
import { ExportDialog } from "./ExportDialog";

const proj = (o: Partial<Project> = {}): Project => ({
  id: "p1", name: "a.wav", created: 0, state: "done", sr: 48000, channels: 2, duration_s: 10, codec: null, lossy: false,
  has_video: false, streams: null, modules: ["speech"], scan: null, result: null, error: null, size_bytes: 0, ...o,
});
const settings = (export_dir: string | null) => ({
  export_dir, data_dir: "D:\\data", models_dir: "D:\\models", data_bytes: 0, models_bytes: 0, free_bytes: 0,
  sources: { data: "settings", models: "default" }, can_browse: true, can_open_folder: true, in_container: false,
  can_restart: true, saved: {}, move: { state: "idle" },
}) as any;

afterEach(() => vi.restoreAllMocks());

function show(p: Project, onProject = vi.fn()) {
  const anchor = createRef<HTMLElement>();
  render(<ExportDialog project={p} float={false} onFloat={() => {}} open onClose={() => {}} anchorRef={anchor} onProject={onProject} />);
  return onProject;
}

test("shows the default folder from Settings, or download-only when none is set", async () => {
  vi.spyOn(apiMod.api, "settings").mockResolvedValue(settings("E:\\Exports"));
  show(proj());
  expect(await screen.findByText("E:\\Exports")).toBeTruthy();
  expect(screen.getByText(/default folder from Settings/)).toBeTruthy();
});

test("a project's own folder wins and can be reset; exporting reports where the files were saved", async () => {
  vi.spyOn(apiMod.api, "settings").mockResolvedValue(settings(null));
  const setDir = vi.spyOn(apiMod.api, "setExportDir").mockResolvedValue(proj({ export_dir: null }));
  vi.spyOn(apiMod.api, "exportFiles").mockResolvedValue({
    files: [{ kind: "speech", name: "a_afix_speech.wav", url: "/x" }], gain_db: 0, subtype: "PCM_24", byte_exact: true,
    zip_url: "/z", can_open_folder: true, folder: "F:\\Own", target: "F:\\Own", target_from: "project",
    saved: ["F:\\Own\\a_afix_speech.wav"], save_error: null,
  });
  const onProject = show(proj({ export_dir: "F:\\Own" }));
  expect(await screen.findByText("F:\\Own")).toBeTruthy();
  expect(screen.getByText(/This project's folder/)).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Export" })); });
  expect(screen.getByText("Saved 1 files to F:\\Own")).toBeTruthy();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "download only" })); });
  expect(setDir).toHaveBeenCalledWith("p1", null);
  expect(onProject).toHaveBeenCalled();
});

test("with nothing to decide it says nothing about levels, waveforms or formats", async () => {
  vi.spyOn(apiMod.api, "settings").mockResolvedValue(settings("E:\\Exports"));
  show(proj({ result: { export_gain_db: 0, byte_exact_possible: true } as any }));
  await screen.findByText("E:\\Exports");
  const text = document.querySelector(".export-modal")!.textContent!;
  expect(text).not.toMatch(/waveform|null test|bit-identical|24-bit|float/i);
  expect(screen.queryByRole("radio")).toBeNull();
});

test("Esc in the folder picker closes the picker and leaves the export window open", async () => {
  vi.spyOn(apiMod.api, "settings").mockResolvedValue(settings("E:\\\\Exports"));
  vi.spyOn(apiMod.api, "dirs").mockResolvedValue({ path: "E:\\\\", parent: null, roots: [], writable: true, dirs: [] });
  const onClose = vi.fn();
  const anchor = createRef<HTMLElement>();
  render(<ExportDialog project={proj()} float={false} onFloat={() => {}} open onClose={onClose} anchorRef={anchor} />);
  fireEvent.click(await screen.findByRole("button", { name: "Choose folder…" }));
  const picker = await screen.findByRole("dialog", { name: /exports go/ });
  fireEvent.keyDown(picker, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: /exports go/ })).toBeNull();
  expect(onClose).not.toHaveBeenCalled();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});
