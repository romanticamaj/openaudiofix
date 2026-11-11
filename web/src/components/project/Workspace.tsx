import { useSyncExternalStore } from "react";
import type { Project } from "../../api";
import { fmtTime } from "../../format";
import { useT } from "../../i18n";
import type { View } from "../../timelineMath";
import { Timeline } from "../Timeline";
import { Minimap } from "./Minimap";
import type { ResultsTransport } from "./useResultsTransport";

/** The done-state centre column: toolbar, waveform, minimap, transport bar and notes. */
export function Workspace({ project, rt, view, onView }: {
  project: Project; rt: ResultsTransport; view: View; onView: (v: View) => void;
}) {
  const { t } = useT();
  const { tp, sources, diffs, finalId, diffId, setDiffId, fixes } = rt;
  // Re-render on every transport change (time, source, play state); kept here so the fix list does not.
  useSyncExternalStore((f) => tp.subscribe(f), () => `${tp.cur}|${tp.playing}|${tp.t.toFixed(2)}|${tp.fixCount}`);

  const res = project.result;
  const sr = project.sr ?? 48000;
  const duration = project.duration_s ?? 0;
  const cur = tp.cur || finalId;
  const isDiff = cur.startsWith("d");
  const curDiff = diffs.find((d) => d.id === (isDiff ? cur : diffId));
  const stages = sources.filter((s) => s.id !== "original" && s.id !== finalId);
  const onStage = stages.some((s) => s.id === cur);

  const seg = (label: string, key: string, pressed: boolean, onClick: () => void, disabled = false) => (
    <button type="button" aria-pressed={pressed} disabled={disabled} onClick={onClick}>
      {label} <span className="kbd" aria-hidden="true">{key}</span>
    </button>
  );

  return (
    <div className="workspace" data-testid="workspace-slot">
      <div className="ws-toolbar" data-transport-keys="">
        <div className="ws-seg" role="group">
          {seg(t("results.original"), "1", cur === "original", () => tp.select("original"))}
          {seg(t("workspace.repaired"), "2", cur === finalId, () => tp.select(finalId))}
          {seg(t("results.diff"), "3", isDiff, () => tp.select(diffId), !diffs.length)}
        </div>
        {sources.length > 2 ? (
          <select className="ws-select" aria-label={t("workspace.stage")} value={onStage ? cur : ""}
                  onChange={(e) => { if (e.target.value) tp.select(e.target.value); }}>
            <option value="" disabled>{t("workspace.stage")}</option>
            {stages.map((s) => <option key={s.id} value={s.id}>{t("results.after", { module: t(`module.${s.module}`) })}</option>)}
          </select>
        ) : null}
        {diffs.length > 1 ? (
          <select className="ws-select" aria-label={t("results.diff")} value={diffId}
                  onChange={(e) => { setDiffId(e.target.value); if (isDiff) tp.select(e.target.value); }}>
            {diffs.map((d) => (
              <option key={d.id} value={d.id}>{d.id === "d_chain" ? t("results.diffChain") : t("results.diffStep", { module: t(`module.${d.module}`) })}</option>
            ))}
          </select>
        ) : null}
        <span className="ws-sp" />
        <span data-testid="fix-counter" className="muted mono">{t("results.fixCounter", { k: tp.fixIndex(), n: tp.fixCount })}</span>
        <button type="button" onClick={rt.prev}><span aria-hidden="true">‹ </span>{t("workspace.prev")}</button>
        <button type="button" onClick={rt.next}>{t("workspace.next")}<span aria-hidden="true"> ›</span></button>
        <button type="button" onClick={() => onView({ t0: 0, t1: duration })}>{t("results.zoomFit")}</button>
      </div>
      <Timeline projectId={project.id} src={cur} rev={res?.rev} duration={duration} sr={sr} fixes={fixes} transport={tp} view={view} onView={onView} />
      <Minimap projectId={project.id} src={cur} rev={res?.rev} duration={duration} view={view} onView={onView} onSeek={(t) => tp.seek(t)} />
      <div className="ws-transport" data-transport-keys="">
        <button type="button" className="ws-play" onClick={() => tp.toggle()} aria-label={tp.playing ? t("results.pause") : t("results.play")}>
          {tp.playing
            ? <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true"><path d="M2.5 1.5h2.5v9H2.5zM7 1.5h2.5v9H7z" /></svg>
            : <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true"><path d="M2.5 1.5v9l8-4.5z" /></svg>}
        </button>
        <span className="ws-tc mono">{fmtTime(tp.t)} / {fmtTime(duration)}</span>
        <span className="ws-sp" />
        <span className="muted ws-keys">{t("results.keys")}</span>
      </div>
      {res && res.preview_gain_db < 0 ? <p className="muted ws-note">{t("results.levelNote", { db: Math.abs(res.preview_gain_db).toFixed(1) })}</p> : null}
      {curDiff?.gain_db != null ? <p className="muted ws-note">{t("results.diffNote", { db: curDiff.gain_db.toFixed(1) })}</p> : null}
      {res?.cleaned ? <p className="muted ws-note">{t("results.cleaned")}</p> : null}
    </div>
  );
}
