import { useCallback, useEffect, useState } from "react";
import { api, type ModuleInfo, type ModuleParams, type ParamSpec, type ParamValue, type Project } from "../../api";
import type { T } from "../../i18n";
import { useToast } from "../ui/Toast";

// Display order glitch -> declip -> denoise -> speech, whatever order the toggles were flipped in; the server runs them
// declip -> glitch -> denoise -> speech.
export const ORDER = ["glitch", "declip", "denoise", "speech"];
export const rank = (id: string) => (ORDER.includes(id) ? ORDER.indexOf(id) : ORDER.length);

/** The scan finding for one module, e.g. "3 dropout seams found"; empty before the scan has run. */
export function foundText(t: T, project: Project, id: string): string {
  const s = project.scan?.[id];
  if (!s) return "";
  if (id === "speech") return t("module.speech.hint");
  if (id === "denoise") return t("module.denoise.found", { db: String(s.summary.noise_floor_dbfs ?? "?") });
  return s.detected ? t(`module.${id}.found`, { count: s.count }) : t("module.clean");
}

export type ModuleChoice = {
  mods: ModuleInfo[];
  on: Record<string, boolean>;
  toggle(id: string): void;
  chosen: string[];
  /** Each module's settings: the schema defaults, then what this viewer last chose, then edits. */
  settings: ModuleParams;
  setSetting(id: string, key: string, value: ParamValue): void;
  /** The settings of the chosen modules that have any, as the run request sends them. */
  params: ModuleParams;
  reload(): void;
};

const STORE = "afix.moduleSettings";
function loadSaved(): ModuleParams {
  try {
    const v = JSON.parse(localStorage.getItem(STORE) ?? "{}");
    return v && typeof v === "object" ? v : {};
  } catch {
    return {};
  }
}
function save(v: ModuleParams) {
  try { localStorage.setItem(STORE, JSON.stringify(v)); } catch { /* private window: settings just are not remembered */ }
}

/** A remembered value only counts while it still fits the module's current schema. */
function fits(spec: ParamSpec, v: unknown) {
  if (spec.type === "boolean") return typeof v === "boolean";
  if (spec.type === "string") return typeof v === "string" && (!spec.enum || spec.enum.includes(v));
  if (typeof v !== "number" || !Number.isFinite(v) || (spec.type === "integer" && !Number.isInteger(v))) return false;
  return v >= (spec.minimum ?? -Infinity) && v <= (spec.maximum ?? Infinity);
}

export function moduleSettings(m: ModuleInfo, saved: Record<string, ParamValue> = {}): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {};
  for (const [k, spec] of Object.entries(m.params?.properties ?? {})) {
    if (fits(spec, saved[k])) out[k] = saved[k];
    else if (spec.default !== undefined) out[k] = spec.default as ParamValue;
  }
  return out;
}

export function useModuleChoice(project: Project, editable: boolean): ModuleChoice {
  const toast = useToast();
  const [mods, setMods] = useState<ModuleInfo[]>([]);
  // Only the toggles the user flipped. Everything else follows the default rule, so a scan that
  // finishes after the page opened still sets the defaults.
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState<ModuleParams>(loadSaved);

  const reload = useCallback(() => {
    api.modules().then((r) => setMods([...r.modules].sort((a, b) => rank(a.id) - rank(b.id))), toast.error);
  }, [toast]);
  useEffect(() => { reload(); }, [reload]);

  // Default on only for a CPU module whose scan found something.
  const dflt = (m: ModuleInfo) => m.device === "cpu" && !!project.scan?.[m.id]?.detected;
  const used = project.modules ?? [];
  // Read-only: show the modules this run actually used.
  const on: Record<string, boolean> = Object.fromEntries(
    mods.map((m) => [m.id, editable ? picked[m.id] ?? dflt(m) : used.includes(m.id)]),
  );
  const toggle = (id: string) => { if (editable) setPicked((p) => ({ ...p, [id]: !on[id] })); };
  // A module counts only when it is installed.
  const chosen = mods.filter((m) => on[m.id] && m.state === "installed").map((m) => m.id);
  const settings: ModuleParams = Object.fromEntries(mods.map((m) => [m.id, moduleSettings(m, saved[m.id])]));
  const setSetting = (id: string, key: string, value: ParamValue) => {
    if (!editable) return;
    setSaved((s) => { const next = { ...s, [id]: { ...settings[id], ...s[id], [key]: value } }; save(next); return next; });
  };
  const params: ModuleParams = Object.fromEntries(
    chosen.filter((id) => Object.keys(settings[id] ?? {}).length).map((id) => [id, settings[id]]),
  );
  return { mods, on, toggle, chosen, settings, setSetting, params, reload };
}
