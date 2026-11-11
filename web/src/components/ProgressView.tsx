import { useState } from "react";
import type { Job, Project } from "../api";
import { useT } from "../i18n";
import { fmtDuration, jobSeconds, overall } from "../progress";
import { ErrorBox } from "./ErrorBox";

/**
 * Step list plus the state error (project.error, details, copy report). Retry and Cancel are not here:
 * the top bar owns them (Cancel while busy, Try again when failed/cancelled).
 */
export function ProgressView({ project }: { project: Project }) {
  const { t } = useT();
  const [copied, setCopied] = useState(false);
  const jobs = project.jobs ?? [];
  const mods = project.modules ?? [];
  const steps: { label: string; job?: Job }[] = mods.map((m, k) => ({
    label: t(`module.${m}`),
    job: jobs.filter((j) => j.kind === "repair" && j.seq === k).at(-1),
  }));
  if (mods.length) steps.push({ label: t("progress.finalize"), job: jobs.filter((j) => j.kind === "finalize").at(-1) });
  const scanJob = jobs.filter((j) => j.kind === "scan").at(-1);
  if (scanJob && scanJob.state !== "done") steps.unshift({ label: t("scan.title"), job: scanJob });
  const failed = project.state === "failed";
  const now = project.now ?? Date.now() / 1000;
  const all = overall(project, now);
  const busy = project.state === "processing" || project.state === "scanning";

  function copyReport() {
    const failedJob = jobs.find((j) => j.state === "failed");
    const rep = {
      app: "OpenAudioFix 0.1.0", module: failedJob?.module, kind: failedJob?.kind, error: project.error,
      input: { codec: project.codec, sr: project.sr, channels: project.channels, duration_s: project.duration_s },
      chain: mods,
    };
    navigator.clipboard?.writeText(JSON.stringify(rep, null, 1)).then(() => setCopied(true), () => {});
  }

  return (
    <section className="card state-card">
      <h3>{t("progress.title")}</h3>
      {busy && steps.length ? (
        <div className="overall">
          <div className="row">
            <strong>{t("progress.overall", { pct: Math.floor(all.pct) })}</strong>
            <span className="muted mono">
              {t("progress.elapsed", { t: fmtDuration(all.elapsed) })}
              {all.remaining != null ? ` · ${t("progress.remaining", { t: fmtDuration(all.remaining) })}` : ""}
            </span>
          </div>
          <div className="bar" role="progressbar" aria-label={t("progress.overallLabel")} aria-valuenow={Math.floor(all.pct)}>
            <i style={{ width: `${Math.max(2, all.pct)}%` }} />
          </div>
        </div>
      ) : null}
      <ol className="steps">
        {steps.map((s, i) => {
          const state = s.job?.state ?? "queued";
          const secs = jobSeconds(s.job, now);
          return (
            <li key={i}>
              <div className="row"><strong>{s.label}</strong>
                <span className="muted">{t(`progress.${state}`)}{s.job?.pct != null && state === "running" ? ` ${s.job.pct.toFixed(0)}%` : ""}
                  {secs != null ? <span className="mono"> · {fmtDuration(secs)}</span> : null}</span></div>
              {state === "running" ? (
                <div className="bar" role="progressbar" aria-valuenow={s.job?.pct ?? undefined}><i style={{ width: `${s.job?.pct ?? 5}%` }} /></div>
              ) : null}
            </li>
          );
        })}
      </ol>
      {failed && project.error ? (
        <>
          <ErrorBox error={project.error} />
          {project.error.detail ? <details><summary>{t("progress.details")}</summary><pre>{project.error.detail}</pre></details> : null}
          <p className="row">
            <button type="button" onClick={copyReport}>{copied ? t("progress.copied") : t("progress.copyReport")}</button>
          </p>
        </>
      ) : null}
    </section>
  );
}
