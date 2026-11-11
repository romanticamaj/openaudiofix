import type { Project } from "../../api";
import { fmtBytes, fmtTime } from "../../format";
import { useT } from "../../i18n";
import { fmtDuration, runTiming } from "../../progress";
import { ModuleCards } from "./ModuleCards";
import { OutputChoice, peaksOver } from "./OutputChoice";
import type { ModuleChoice } from "./useModuleChoice";

export function Inspector({ project: p, choice, editable, float, onFloat }: {
  project: Project; choice: ModuleChoice; editable: boolean; float: boolean; onFloat: (float: boolean) => void;
}) {
  const { t } = useT();
  const done = p.state === "done";
  const rows: [string, string][] = [
    [t("inspector.duration"), p.duration_s != null ? fmtTime(p.duration_s) : "–"],
    [t("inspector.sampleRate"), p.sr ? `${+(p.sr / 1000).toFixed(1)} kHz` : "–"],
    [t("inspector.channels"), p.channels ? String(p.channels) : "–"],
    [t("inspector.format"), p.codec ?? "–"],
    [t("inspector.size"), fmtBytes(p.size_bytes ?? 0)],
  ];
  // time taken by each step of the finished run, and how that compares with the file's length
  const run = done ? runTiming(p) : { steps: [], total: 0 };
  const timing = run.steps.map((r) => ({ label: r.module ? t(`module.${r.module}`) : t("progress.finalize"), secs: r.secs }));
  const total = run.total;
  return (
    <aside className="inspector">
      <section className="insp-sec">
        <h6>{t("inspector.file")}</h6>
        <dl className="kv">
          {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
        {p.has_video || p.lossy ? (
          <div className="insp-tags">
            {p.has_video ? <span className="tag">{t("inspector.video")}</span> : null}
            {p.lossy ? <span className="tag warn">{t("inspector.lossy")}</span> : null}
          </div>
        ) : null}
      </section>
      <section className="insp-sec">
        <h6>{t("inspector.modules")} <span>{t("inspector.modulesOrder")}</span></h6>
        <ModuleCards project={p} choice={choice} editable={editable} />
      </section>
      {timing.length ? (
        <section className="insp-sec">
          <h6>{t("inspector.timing")}</h6>
          <dl className="kv">
            {timing.map((r, i) => <div key={i}><dt>{r.label}</dt><dd>{fmtDuration(r.secs)}</dd></div>)}
            <div className="kv-total"><dt>{t("inspector.timingTotal")}</dt><dd>{fmtDuration(total)}</dd></div>
            {p.duration_s && total > 0 ? (
              <div><dt>{t("inspector.speed")}</dt><dd>{t("inspector.speedValue", { x: (p.duration_s / total).toFixed(1) })}</dd></div>
            ) : null}
          </dl>
        </section>
      ) : null}
      {peaksOver(p) && !editable ? (
        <section className="insp-sec">
          <h6>{t("inspector.output")}</h6>
          <OutputChoice project={p} float={float} onFloat={onFloat} />
        </section>
      ) : null}
    </aside>
  );
}
