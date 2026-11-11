import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, notifyProjectsChanged, type FixRow, type Project } from "../api";
import { ErrorBox } from "../components/ErrorBox";
import { ProgressView } from "../components/ProgressView";
import { StreamPicker } from "../components/StreamPicker";
import { ExportDialog } from "../components/project/ExportDialog";
import { FixList } from "../components/project/FixList";
import { peaksOver } from "../components/project/OutputChoice";
import { Inspector } from "../components/project/Inspector";
import { RenameInline } from "../components/project/RenameInline";
import { DoneSummary } from "../components/project/DoneSummary";
import { DetectionSummary, FailedCard, ScanCard } from "../components/project/StatePanels";
import { useModuleChoice } from "../components/project/useModuleChoice";
import { useResultsTransport } from "../components/project/useResultsTransport";
import { Workspace } from "../components/project/Workspace";
import { Topbar } from "../components/shell/Topbar";
import { Badge } from "../components/ui/Badge";
import { Menu } from "../components/ui/Menu";
import { Popover } from "../components/ui/Popover";
import { useToast } from "../components/ui/Toast";
import { useT } from "../i18n";
import { go, href } from "../router";
import type { View } from "../timelineMath";

const POLL = new Set(["scanning", "processing"]);
const is404 = (e: unknown) => e instanceof ApiError && e.status === 404;

// Keyed by id so moving to another project (sidebar) starts from a clean slate.
export function ProjectPage({ id }: { id: string }) {
  return <ProjectPageInner key={id} id={id} />;
}

function ProjectPageInner({ id }: { id: string }) {
  const { t } = useT();
  const [p, setP] = useState<Project | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [rechoose, setRechoose] = useState(false);
  const [floatPick, setFloat] = useState<boolean | null>(null); // the person's choice, when there is one
  const inflight = useRef(false);

  const load = useCallback(async () => {
    if (inflight.current) return; // never overlap polls
    inflight.current = true;
    try { setP(await api.project(id)); setErr(null); } catch (e) {
      if (is404(e)) setNotFound(true); else setErr(e as ApiError);
    } finally { inflight.current = false; }
  }, [id]);
  useEffect(() => { load(); }, [load]);
  const polling = !!p && POLL.has(p.state) && !notFound;
  useEffect(() => {
    if (!polling) return;
    const h = setInterval(load, 1000); // keeps going after a failed poll; the next success clears the error
    return () => clearInterval(h);
  }, [polling, load]);

  const back = <a href={href({ name: "home" })}>{t("nav.projects")}</a>;
  if (notFound) {
    return (
      <>
        <Topbar crumbs={[back]} />
        <div className="notfound card">
          <p>{t("project.notFound")}</p>
          <a href={href({ name: "home" })}>← {t("common.back")}</a>
        </div>
      </>
    );
  }
  if (!p) {
    return (
      <>
        <Topbar crumbs={[back]} />
        {err ? <ErrorBox error={err} /> : <p className="muted">{t("common.loading")}</p>}
      </>
    );
  }
  // Float is only ever needed for peaks over full scale; there it keeps the level and waveform, so it is the default.
  const float = peaksOver(p) ? floatPick ?? true : false;
  return (
    <ProjectView p={p} setP={setP} err={err} load={load} rechoose={rechoose} setRechoose={setRechoose}
               float={float} setFloat={setFloat} back={back} />
  );
}

function ProjectView({ p, setP, err, load, rechoose, setRechoose, float, setFloat, back }: {
  p: Project; setP: (p: Project) => void; err: ApiError | null; load: () => Promise<void>;
  rechoose: boolean; setRechoose: (v: boolean) => void; float: boolean; setFloat: (v: boolean) => void;
  back: JSX.Element;
}) {
  const { t } = useT();
  const toast = useToast();
  const choosing = p.state === "ready" || rechoose;
  const choice = useModuleChoice(p, choosing);
  const [busy, setBusy] = useState(false);
  const [confirmRun, setConfirmRun] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [menuConfirm, setMenuConfirm] = useState<"clean" | "delete" | null>(null);
  const startRef = useRef<HTMLButtonElement>(null);
  const exportRef = useRef<HTMLButtonElement>(null);
  const [exportOpen, setExportOpen] = useState(false);

  /** Run one action: errors go to a toast; success refreshes the sidebar and this page. */
  async function act(fn: () => Promise<Project | unknown>, after?: () => void) {
    setBusy(true);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "state" in r) setP(r as Project);
      notifyProjectsChanged();
      after?.();
      await load();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  }
  const run = () => act(() => api.run(p.id, choice.chosen, choice.params), () => { setConfirmRun(false); setRechoose(false); });
  const cancel = () => act(() => api.cancel(p.id));
  const retry = () => act(() => api.retry(p.id));
  const clean = () => act(() => api.clean(p.id));
  const chooseStream = (index: number) => act(() => api.chooseStream(p.id, index));
  async function remove() {
    try { await api.remove(p.id); notifyProjectsChanged(); go({ name: "home" }); } catch (e) { toast.error(e); }
  }
  async function rename(name: string) {
    try {
      setP(await api.rename(p.id, name));
      notifyProjectsChanged();
      await load();
    } catch (e) {
      toast.error(e);
      throw e; // RenameInline puts the old name back
    }
  }

  let actions: JSX.Element | null = null;
  if (choosing) {
    actions = (
      <>
        {rechoose ? <button type="button" onClick={() => { setRechoose(false); setConfirmRun(false); }}>{t("common.cancel")}</button> : null}
        <span className="pop-anchor">
          <button ref={startRef} type="button" className="primary" disabled={busy || choice.chosen.length === 0}
                  onClick={() => (rechoose ? setConfirmRun(true) : run())}>
            {t("choose.start")}
          </button>
          <Popover open={confirmRun} onClose={() => setConfirmRun(false)} anchorRef={startRef}>
            <p className="pop-text">{t("project.reprocessConfirm")}</p>
            <div className="dialog-actions">
              <button type="button" onClick={() => setConfirmRun(false)}>{t("common.cancel")}</button>
              <button type="button" className="primary" disabled={busy} onClick={run}>{t("project.confirmStart")}</button>
            </div>
          </Popover>
        </span>
      </>
    );
  } else if (POLL.has(p.state)) {
    actions = <button type="button" disabled={busy} onClick={cancel}>{t("common.cancel")}</button>;
  } else if (p.state === "done") {
    const cleaned = !!p.result?.cleaned;
    actions = (
      <>
        <button type="button" onClick={() => setRechoose(true)}>{t("project.reprocess")}</button>
        <span className="pop-anchor">
          <button ref={exportRef} type="button" className="primary" aria-expanded={exportOpen} onClick={() => setExportOpen((o) => !o)}>
            {t("export.go")}
          </button>
          <ExportDialog project={p} float={float} onFloat={setFloat} open={exportOpen} onClose={() => setExportOpen(false)} anchorRef={exportRef} onProject={setP} />
        </span>
        <Menu
          label={t("projects.actions")}
          onOpenChange={(open) => { if (!open) setMenuConfirm(null); }}
          items={[
            { label: t("project.rename"), onSelect: () => setRenaming(true) },
            {
              label: menuConfirm === "clean" ? t("project.cleanConfirm") : t("project.clean"),
              disabled: cleaned || busy,
              keepOpen: menuConfirm !== "clean",
              onSelect: () => { if (menuConfirm === "clean") void clean(); else setMenuConfirm("clean"); },
            },
            {
              label: menuConfirm === "delete" ? t("project.deleteConfirm") : t("project.delete"),
              danger: true,
              keepOpen: menuConfirm !== "delete",
              onSelect: () => { if (menuConfirm === "delete") void remove(); else setMenuConfirm("delete"); },
            },
          ]}
        />
      </>
    );
  } else if (p.state === "failed" || p.state === "cancelled") {
    actions = <button type="button" className="primary" disabled={busy} onClick={retry}>{t("project.tryAgain")}</button>;
  }

  const done = p.state === "done" && !choosing;
  let centre: JSX.Element | null = null;
  if (p.state === "needs_stream") centre = <StreamPicker project={p} onChoose={chooseStream} busy={busy} />;
  else if (p.state === "scanning") centre = <ScanCard project={p} onCancel={cancel} busy={busy} />;
  else if (choosing) centre = <DetectionSummary project={p} />;
  else if (p.state === "processing") centre = <ProgressView project={p} />;
  else if (!done) centre = <FailedCard project={p} />;

  return (
    <>
      <Topbar
        crumbs={[back, <RenameInline key="name" name={p.name} onSave={rename} editing={renaming} onEditingChange={setRenaming} />]}
        badge={<Badge state={p.state} />}
        actions={actions}
      />
      <div className={done ? "project-grid with-list" : "project-grid"}>
        <Inspector project={p} choice={choice} editable={choosing} float={float} onFloat={setFloat} />
        {done ? <DoneArea project={p} err={err} /> : (
          <div className="project-centre">
            <ErrorBox error={err} />
            {centre}
          </div>
        )}
      </div>
    </>
  );
}

/**
 * The centre and right columns of a done project. Between 760 and 1099px a tab bar switches them;
 * outside that range the tab bar is hidden (wide) or both panels stack under it (narrow).
 */
function DoneArea({ project, err }: { project: Project; err: ApiError | null }) {
  const { t } = useT();
  const [selected, setSelected] = useState<number | null>(null); // index into rt.points
  const rt = useResultsTransport(project, setSelected);
  const duration = project.duration_s ?? 0;
  const [view, setView] = useState<View>({ t0: 0, t1: duration });
  useEffect(() => { setView({ t0: 0, t1: duration }); }, [duration]);
  const [tab, setTab] = useState<"wave" | "fixes">("wave");
  const { tp, points } = rt;
  const onPick = useCallback((seconds: number, row: FixRow) => {
    tp.seekToFix(seconds);
    const i = points.indexOf(row);
    setSelected(i < 0 ? null : i);
  }, [tp, points]);

  const tabBtn = (id: "wave" | "fixes", label: string) => (
    <button type="button" role="tab" id={`tab-${id}`} aria-controls={`panel-${id}`} aria-selected={tab === id} onClick={() => setTab(id)}>
      {label}
    </button>
  );
  return (
    <>
      <div className="narrow-tabs" role="tablist">
        {tabBtn("wave", t("tabs.wave"))}
        {tabBtn("fixes", t("tabs.fixes"))}
      </div>
      <div id="panel-wave" role="tabpanel" aria-labelledby="tab-wave" className={tab === "wave" ? "project-centre" : "project-centre is-hidden"}>
        <ErrorBox error={err} />
        <DoneSummary project={project} />
        <Workspace project={project} rt={rt} view={view} onView={setView} />
      </div>
      <div id="panel-fixes" role="tabpanel" aria-labelledby="tab-fixes" className={tab === "fixes" ? "fix-col" : "fix-col is-hidden"}>
        <FixList fixes={rt.fixes} selected={selected != null ? points[selected] ?? null : null} onPick={onPick}
                 sr={project.sr ?? 48000} duration={duration} />
      </div>
    </>
  );
}
