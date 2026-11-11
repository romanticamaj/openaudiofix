import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, notifyProjectsChanged, upload, type ModuleInfo, type Project } from "../api";
import { DropOverlay } from "../components/projects/DropOverlay";
import { ProjectTable, type Uploading } from "../components/projects/ProjectTable";
import { RenameDialog } from "../components/projects/RenameDialog";
import { StatCards } from "../components/projects/StatCards";
import { Topbar } from "../components/shell/Topbar";
import { useToast } from "../components/ui/Toast";
import { fmtBytes } from "../format";
import { useT } from "../i18n";
import { go } from "../router";
import { filterProjects, groupCounts, sortProjects, type GroupFilter, type Sort } from "../projectFilter";
import { gpuSummary, projectFixCount, stateGroup } from "../status";

const FILTERS: GroupFilter[] = ["all", "done", "busy", "waiting", "failed"];
const SORT_KEY = "afix.projectSort";

function loadSort(): Sort {
  try {
    const s = JSON.parse(localStorage.getItem(SORT_KEY) ?? "null");
    if (s && typeof s.key === "string" && (s.dir === "asc" || s.dir === "desc")) return s;
  } catch { /* default */ }
  return { key: "created", dir: "desc" };
}

export function ProjectsPage() {
  const { t } = useT();
  const toast = useToast();
  const [list, setList] = useState<Project[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [usage, setUsage] = useState({ total: 0, free: 0 });
  const [gpu, setGpu] = useState<ModuleInfo | undefined>();
  const [q, setQ] = useState("");
  const [group, setGroup] = useState<GroupFilter>("all");
  const [sort, setSortState] = useState<Sort>(loadSort);
  const setSort = (s: Sort) => {
    setSortState(s);
    try { localStorage.setItem(SORT_KEY, JSON.stringify(s)); } catch { /* not kept */ }
  };
  const [up, setUp] = useState<Uploading | null>(null);
  const [renaming, setRenaming] = useState<Project | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadingRef = useRef(false);

  // silent: background polling never toasts, so a down server cannot stack errors every 3 s.
  const load = useCallback(async (silent = false) => {
    try {
      const r = await api.projects();
      setList(r.projects);
      setUsage({ total: r.total_bytes, free: r.free_bytes });
      setLoaded(true);
    } catch (e) { if (!silent) toast.error(e); }
  }, [toast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api.modules().then((r) => setGpu(r.modules.find((m) => m.device === "gpu"))).catch(() => {});
  }, []);
  useEffect(() => {
    if (!list.some((p) => stateGroup(p.state) === "busy")) return;
    const id = setInterval(() => load(true), 3000);
    return () => clearInterval(id);
  }, [list, load]);

  const send = useCallback(async (file: File | undefined) => {
    if (!file) return;
    if (uploadingRef.current) { toast.info(t("projects.uploadBusy")); return; }
    uploadingRef.current = true;
    setUp({ name: file.name, pct: 0 });
    try {
      const p = await upload(file, (pct) => setUp({ name: file.name, pct }));
      notifyProjectsChanged();
      go({ name: "project", id: p.id });
    } catch (e) {
      toast.error(e);
      load(); // refresh usage/free space so old projects can be judged for deletion (e.g. after disk_full)
    } finally { uploadingRef.current = false; setUp(null); }
  }, [toast, load, t]);

  async function rename(p: Project, name: string) {
    try {
      await api.rename(p.id, name);
      setRenaming(null);
      await load();
      notifyProjectsChanged();
    } catch (e) { toast.error(e); }
  }

  async function remove(p: Project) {
    try { await api.remove(p.id); await load(); notifyProjectsChanged(); } catch (e) { toast.error(e); }
  }

  const counts = useMemo(() => groupCounts(list), [list]);
  const shown = useMemo(() => sortProjects(filterProjects(list, q, group), sort, projectFixCount), [list, q, group, sort]);
  const fixes = useMemo(
    () => list.filter((p) => p.state === "done").reduce((a, p) => a + projectFixCount(p), 0),
    [list],
  );
  const g = gpuSummary(gpu);

  return (
    <>
      <Topbar
        crumbs={[t("nav.projects")]}
        actions={
          <button type="button" className="primary" onClick={() => fileInput.current?.click()} disabled={up !== null}>
            {t("projects.upload")}
          </button>
        }
      />
      <input
        ref={fileInput}
        type="file"
        hidden
        data-testid="file-input"
        accept="audio/*,video/*,.wav,.flac,.mp3,.m4a,.mp4,.mkv,.mov"
        onChange={(e) => { send(e.target.files?.[0]); e.target.value = ""; }}
        disabled={up !== null}
      />
      <DropOverlay onFile={send} />
      <StatCards
        stats={[
          { key: "projects", label: t("projects.stat.projects"), value: counts.all },
          { key: "fixes", label: t("projects.stat.fixes"), value: fixes },
          { key: "busy", label: t("projects.stat.busy"), value: counts.busy },
          { key: "gpu", label: t("projects.stat.gpu"), value: t(g.key, g.vars), text: true },
        ]}
      />
      <div className="toolbar">
        <input
          type="search"
          className="search"
          aria-label={t("projects.search")}
          placeholder={t("projects.search")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="row">
          {FILTERS.map((f) => (
            <button key={f} type="button" className="chip" aria-pressed={group === f} onClick={() => setGroup(f)}>
              {t(`projects.filter.${f}`)} <span className="mono">{counts[f]}</span>
            </button>
          ))}
        </div>
      </div>
      {loaded && list.length === 0 && !up ? (
        <div className="empty-drop"><p>{t("projects.empty")}</p></div>
      ) : (
        <ProjectTable projects={shown} uploading={up} noMatch={list.length > 0 && shown.length === 0} onRename={setRenaming} onDelete={remove}
                      sort={sort} onSort={setSort} />
      )}
      <p className="muted footer-line">{t("projects.usage", { used: fmtBytes(usage.total), free: fmtBytes(usage.free) })}</p>
      {renaming && (
        <RenameDialog initial={renaming.name} onSave={(name) => rename(renaming, name)} onCancel={() => setRenaming(null)} />
      )}
    </>
  );
}
