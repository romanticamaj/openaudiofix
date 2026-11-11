import { useCallback, useEffect, useState } from "react";
import { api, MODULES_CHANGED, PROJECTS_CHANGED, type ModuleInfo, type Project } from "../../api";
import { useT } from "../../i18n";
import { href, type Route } from "../../router";
import { stateGroup, TONE } from "../../status";
import { GpuStatusCard } from "./GpuStatusCard";

const BUSY = new Set(["scanning", "processing"]);

export function Sidebar({ route, open, onClose }: { route: Route; open: boolean; onClose: () => void }) {
  const { t } = useT();
  const [projects, setProjects] = useState<Project[]>([]);
  const [modules, setModules] = useState<ModuleInfo[]>([]);

  const loadProjects = useCallback(() => { api.projects().then((r) => setProjects(r.projects)).catch(() => {}); }, []);
  const loadModules = useCallback(() => { api.modules().then((r) => setModules(r.modules)).catch(() => {}); }, []);

  useEffect(() => {
    loadProjects(); loadModules();
    const onHash = () => { loadProjects(); loadModules(); };
    // A live GPU state (from the Modules page SSE) replaces the GPU entry directly, with no network call.
    const onModule = (e: Event) => {
      const m = (e as CustomEvent<ModuleInfo>).detail;
      if (!m) return;
      setModules((prev) => {
        const i = prev.findIndex((x) => x.id === m.id || x.device === "gpu");
        const next = { ...(i >= 0 ? prev[i] : {}), ...m, device: "gpu" } as ModuleInfo;
        return i >= 0 ? prev.map((x, j) => (j === i ? next : x)) : [...prev, next];
      });
    };
    addEventListener("hashchange", onHash);
    addEventListener(PROJECTS_CHANGED, loadProjects);
    addEventListener(MODULES_CHANGED, onModule);
    return () => {
      removeEventListener("hashchange", onHash);
      removeEventListener(PROJECTS_CHANGED, loadProjects);
      removeEventListener(MODULES_CHANGED, onModule);
    };
  }, [loadProjects, loadModules]);

  const busy = projects.some((p) => BUSY.has(p.state));
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(loadProjects, 3000);
    return () => clearInterval(id);
  }, [busy, loadProjects]);

  const recent = [...projects].sort((a, b) => b.created - a.created).slice(0, 5);
  const gpu = modules.find((m) => m.device === "gpu");
  const cur = (on: boolean) => (on ? "page" : undefined);

  return (
    <aside className={`sidebar${open ? " open" : ""}`} aria-label={t("nav.label")}>
      <a className="brand" href="#/" onClick={onClose}>
        <span className="brand-mark">OA</span>
        <span>OpenAudioFix</span>
      </a>
      <nav className="nav">
        <a href={href({ name: "home" })} onClick={onClose} aria-current={cur(route.name === "home" || route.name === "project")}>
          <span>{t("nav.projects")}</span><span className="nav-count">{projects.length}</span>
        </a>
        <a href={href({ name: "modules" })} onClick={onClose} aria-current={cur(route.name === "modules")}>{t("nav.modules")}</a>
        <a href={href({ name: "settings" })} onClick={onClose} aria-current={cur(route.name === "settings")}>{t("nav.settings")}</a>
      </nav>
      {recent.length > 0 && (
        <div className="recent">
          <div className="recent-title">{t("nav.recent")}</div>
          {recent.map((p) => (
            <a key={p.id} href={`#/p/${p.id}`} className="recent-item" onClick={onClose}>
              <span className={`dot ${TONE[stateGroup(p.state)]}`} />{p.name}
            </a>
          ))}
        </div>
      )}
      <div className="sidebar-foot"><GpuStatusCard module={gpu} onClick={onClose} /></div>
    </aside>
  );
}
