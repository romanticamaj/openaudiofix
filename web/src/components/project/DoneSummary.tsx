import { useState } from "react";
import type { Project } from "../../api";
import { useT } from "../../i18n";
import { fmtDuration, runTiming } from "../../progress";

const KEY = "afix.doneSummaryClosed";

function closedIds(): string[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]"); } catch { return []; }
}

/** The finished run at a glance: total time, speed against real time and each step's time. Closable per project. */
export function DoneSummary({ project: p }: { project: Project }) {
  const { t } = useT();
  const [closed, setClosed] = useState(() => closedIds().includes(p.id));
  const run = runTiming(p);
  if (closed || !run.steps.length) return null;
  const close = () => {
    setClosed(true);
    try { localStorage.setItem(KEY, JSON.stringify([...closedIds().filter((x) => x !== p.id), p.id].slice(-200))); } catch { /* not kept */ }
  };
  const speed = p.duration_s && run.total > 0 ? p.duration_s / run.total : null;
  return (
    <section className="done-summary" aria-label={t("done.title")}>
      <span className="done-ic" aria-hidden="true">✓</span>
      <div className="done-main">
        <strong>{t("done.title")}</strong>
        <span className="done-stats">
          <span>{t("done.total", { t: fmtDuration(run.total) })}</span>
          {speed ? <span>{t("inspector.speedValue", { x: speed.toFixed(1) })}</span> : null}
        </span>
        <span className="done-steps mono">
          {run.steps.map((r, i) => (
            <span key={i} className="done-step">
              {r.module ? <span className="dot" style={{ background: `var(--fix-${r.module})` }} aria-hidden="true" /> : null}
              {r.module ? t(`module.${r.module}`) : t("progress.finalize")} {fmtDuration(r.secs)}
            </span>
          ))}
        </span>
      </div>
      <button type="button" className="icon-btn" aria-label={t("done.close")} onClick={close}>×</button>
    </section>
  );
}
