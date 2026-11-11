export type ApiErr = { code: string; message: string; hint?: string; detail?: string; module?: string | null; kind?: string };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public hint = "") {
    super(message);
  }
}

export type Health = { ok: boolean; checks: { id: string; ok: boolean; message: string }[] };
export type ScanEntry = { module: string; detected: boolean; count: number; summary: Record<string, unknown> };
export type Job = {
  id: number; kind: "scan" | "repair" | "finalize"; module: string | null; seq: number; state: string;
  pct: number | null; message: string | null; error: ApiErr | null; attempts: number;
  started?: number | null; finished?: number | null;
};
export type Stream = { index: number; codec: string; sr: number; channels: number };
export type SourceRef = { id: string; module?: string | null; gain_db?: number | null };
export type Result = {
  preview_gain_db: number; export_gain_db: number; sources: SourceRef[]; diffs: SourceRef[];
  counts: Record<string, number>; notes: Record<string, Record<string, unknown>>; byte_exact_possible: boolean;
  cleaned?: boolean; timings?: Record<string, number>;
  /** Changes with every run: re-processing reuses file names, so URLs carry this to get past any cache. */
  rev?: number;
};
export type Project = {
  id: string; name: string; created: number; state: string; sr: number | null; channels: number | null;
  duration_s: number | null; codec: string | null; lossy: boolean | null; has_video: boolean | null;
  streams: Stream[] | null; modules: string[] | null; params?: ModuleParams | null; scan: Record<string, ScanEntry> | null;
  result: Result | null; error: ApiErr | null; size_bytes: number; jobs?: Job[];
  /** In the list: seconds the finished run took (repair + finalize steps). */
  run_s?: number | null;
  subtype?: string | null;
  /** The project's own export folder (null: the default from Settings). */
  export_dir?: string | null;
  /** The server clock (epoch seconds) when this was read. */
  now?: number;
  /** While busy: seconds per audio second each step took before, keyed "kind:module" ("finalize:"). */
  rates?: Record<string, number>;
};
export type ModuleInfo = {
  id: string; name: string; device: "cpu" | "gpu"; state: string; step: string | null; detail: Record<string, any>; updated?: number | null;
  params?: { properties?: Record<string, ParamSpec> };
};
export type ModelInfo = {
  id: string; name: string; used_by: string[]; license: string; state: "ready" | "missing" | "corrupt";
  size_bytes: number; expected_bytes: number; path: string | null; managed_by: string | null; device: "cpu" | "gpu";
  versions?: Record<string, string>;
};
export type ParamSpec = { type: "integer" | "number" | "string" | "boolean"; default?: unknown; minimum?: number; maximum?: number; enum?: string[] };
export type ParamValue = number | string | boolean;
export type ModuleParams = Record<string, Record<string, ParamValue>>;
export type FixRow = { module: string; kind: string; channel: number; start: number; end: number; seconds: number };
export type ExportResult = { files: { kind: string; name: string; url: string }[]; gain_db: number; subtype: string; byte_exact: boolean;
  zip_url: string; can_open_folder: boolean; folder: string | null;
  /** Where the files were also saved: the project's own folder or the default (null: downloads only). */
  target: string | null; target_from: "project" | "default" | null; saved: string[]; save_error: string | null };

export type FolderListing = { path: string | null; parent: string | null; dirs: { name: string; path: string }[];
  roots: string[]; writable: boolean };
export type MoveState = { state: "idle" | "copying" | "done" | "failed"; what?: "data" | "models"; to?: string;
  done_bytes?: number; total_bytes?: number | null; error?: string };
export type LocationSource = "env" | "settings" | "default";
export type AppSettings = {
  export_dir: string | null; data_dir: string; models_dir: string; data_bytes: number; models_bytes: number;
  free_bytes: number | null; sources: { data: LocationSource; models: LocationSource };
  can_browse: boolean; can_open_folder: boolean; in_container: boolean; can_restart: boolean;
  /** Folders saved for the next start (location.json). */
  saved: { data?: string; models?: string }; move: MoveState;
  /** The usual folders on this system (export: null in a container). */
  defaults: { data: string; models: string; export: string | null };
  /** True for a folder inside the system temp folder, which cleanup tools may empty. */
  in_temp: { data: boolean; models: boolean };
};

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "network error");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.code ?? "internal_error", data.message ?? r.statusText, data.hint ?? "");
  return data as T;
}

export function upload(file: File, onProgress: (pct: number) => void): Promise<Project> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api/projects");
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    x.onload = () => {
      let d: any = {};
      try { d = JSON.parse(x.responseText); } catch { /* not json */ }
      if (x.status < 300) resolve(d);
      else reject(new ApiError(x.status, d.code ?? "internal_error", d.message ?? "", d.hint ?? ""));
    };
    x.onerror = () => reject(new ApiError(0, "network", "network error"));
    const fd = new FormData();
    fd.append("file", file);
    x.send(fd);
  });
}

const P = (id: string) => `/api/projects/${id}`;

export const api = {
  health: () => req<Health>("GET", "/api/health"),
  projects: () => req<{ projects: Project[]; total_bytes: number; free_bytes: number }>("GET", "/api/projects"),
  project: (id: string) => req<Project>("GET", P(id)),
  rename: (id: string, name: string) => req<Project>("PATCH", P(id), { name }),
  chooseStream: (id: string, index: number) => req<Project>("POST", `${P(id)}/stream`, { index }),
  run: (id: string, modules: string[], params: ModuleParams = {}) => req<Project>("POST", `${P(id)}/run`, { modules, params }),
  cancel: (id: string) => req<Project>("POST", `${P(id)}/cancel`),
  retry: (id: string) => req<Project>("POST", `${P(id)}/retry`),
  remove: (id: string) => req<{ ok: boolean }>("DELETE", P(id)),
  clean: (id: string) => req<Project>("POST", `${P(id)}/clean`),
  fixes: (id: string) => req<{ fixes: FixRow[] }>("GET", `${P(id)}/fixes`),
  exportFiles: (id: string, float: boolean) => req<ExportResult>("POST", `${P(id)}/export`, { float }),
  setExportDir: (id: string, dir: string | null) =>
    req<Project>("PATCH", P(id), dir ? { export_dir: dir } : { clear_export_dir: true }),
  settings: () => req<AppSettings>("GET", "/api/settings"),
  saveSettings: (s: { export_dir: string | null }) => req<AppSettings>("PUT", "/api/settings", s),
  openSettingFolder: (what: "data" | "models" | "export") => req<{ folder: string }>("POST", `/api/settings/open?what=${what}`),
  moveFolder: (what: "data" | "models", path: string) => req<AppSettings>("POST", "/api/settings/move", { what, path }),
  restart: () => req<{ ok: boolean }>("POST", "/api/restart"),
  dirs: (path = "") => req<FolderListing>("GET", `/api/fs/dirs?path=${encodeURIComponent(path)}`),
  mkdir: (path: string, name: string) => req<FolderListing>("POST", "/api/fs/mkdir", { path, name }),
  openExports: (id: string) => req<{ folder: string }>("POST", `${P(id)}/open-exports`),
  models: () => req<{ dir: string; models: ModelInfo[]; can_open_folder: boolean }>("GET", "/api/models"),
  downloadModel: (id: string) => req<ModelInfo>("POST", `/api/models/${id}/download`),
  deleteModel: (id: string) => req<ModelInfo>("DELETE", `/api/models/${id}`),
  modules: () => req<{ modules: ModuleInfo[] }>("GET", "/api/modules"),
  install: (confirm: boolean) => req<ModuleInfo>("POST", "/api/modules/denoise/install", { confirm }),
  verify: () => req<ModuleInfo>("POST", "/api/modules/denoise/verify"),
  uninstall: () => req<ModuleInfo>("DELETE", "/api/modules/denoise"),
};

export const PROJECTS_CHANGED = "afix:projects-changed";
export function notifyProjectsChanged(): void {
  window.dispatchEvent(new Event(PROJECTS_CHANGED));
}

/** A GPU module state seen live (SSE on the Modules page); the Sidebar card takes it from `detail`. */
export const MODULES_CHANGED = "afix:modules-changed";
export function notifyModulesChanged(m: ModuleInfo): void {
  window.dispatchEvent(new CustomEvent<ModuleInfo>(MODULES_CHANGED, { detail: m }));
}

export const urls = {
  file: (id: string, rel: string) => `${P(id)}/files/${rel}`,
  preview: (id: string, src: string, rev = 0) => `${P(id)}/files/preview/${src}.webm${rev ? `?v=${rev}` : ""}`,
  overviewPeaks: (id: string, src: string, rev = 0) => `${P(id)}/files/peaks/${src}.json${rev ? `?v=${rev}` : ""}`,
  peaks: (id: string, src: string, t0: number, t1: number, n: number, rev = 0) =>
    `${P(id)}/peaks?src=${src}&t0=${t0.toFixed(3)}&t1=${t1.toFixed(3)}&n=${n}${rev ? `&v=${rev}` : ""}`,
  spectrogram: (id: string, src: string, t0: number, t1: number, w: number, h: number, rev = 0) =>
    `${P(id)}/spectrogram?src=${src}&t0=${t0.toFixed(3)}&t1=${t1.toFixed(3)}&w=${w}&h=${h}${rev ? `&v=${rev}` : ""}`,
  installEvents: "/api/modules/denoise/install/events",
};
