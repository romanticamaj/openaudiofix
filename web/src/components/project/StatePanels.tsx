import type { Project } from "../../api";
import { useT } from "../../i18n";
import { ProgressView } from "../ProgressView";
import { ModuleIcon } from "./ModuleCards";
import { foundText, rank } from "./useModuleChoice";

/** Scanning: the step name, an indeterminate bar, and Cancel. */
export function ScanCard({ project, onCancel, busy }: { project: Project; onCancel: () => void; busy?: boolean }) {
  const { t } = useT();
  const job = (project.jobs ?? []).filter((j) => j.kind === "scan").at(-1);
  return (
    <section className="card state-card">
      <h3>{t("scan.title")}</h3>
      {job?.message ? <p className="muted">{job.message}</p> : null}
      <div className="bar" role="progressbar" aria-label={t("scan.title")}><i className="indeterminate" /></div>
      <p><button type="button" disabled={busy} onClick={onCancel}>{t("common.cancel")}</button></p>
    </section>
  );
}

/** Ready (or reprocessing): what the scan found per module, plus how to go on. No waveform yet. */
export function DetectionSummary({ project }: { project: Project }) {
  const { t } = useT();
  const entries = Object.values(project.scan ?? {}).sort((a, b) => rank(a.module) - rank(b.module));
  const nothing = !!project.scan && !entries.some((s) => s.detected && s.module !== "denoise");
  return (
    <section className="card state-card">
      <h3>{t("detect.title")}</h3>
      {project.lossy ? <p className="error" role="note">{t("choose.lossy", { codec: project.codec ?? "?" })}</p> : null}
      <ul className="detect-list">
        {entries.map((s) => (
          <li key={s.module}>
            <ModuleIcon id={s.module} />
            <b>{t(`module.${s.module}`)}</b>
            <span className="muted">{foundText(t, project, s.module)}</span>
          </li>
        ))}
      </ul>
      {nothing ? <p className="muted">{t("choose.nothing")}</p> : null}
      <p className="muted hint">{t("detect.hint")}</p>
    </section>
  );
}

/** Failed or cancelled: the progress card (it shows project.error; Try again is in the top bar), with a note when cancelled. */
export function FailedCard({ project }: { project: Project }) {
  const { t } = useT();
  return (
    <>
      {project.state === "cancelled" ? <p className="card state-card muted">{t("project.cancelledNote")}</p> : null}
      <ProgressView project={project} />
    </>
  );
}
