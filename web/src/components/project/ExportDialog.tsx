import { type RefObject, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type AppSettings, type ExportResult, type Project } from "../../api";
import { useT } from "../../i18n";
import { FolderPicker } from "../FolderPicker";
import { OutputChoice, peaksOver } from "./OutputChoice";
import { useToast } from "../ui/Toast";

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]';

function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.4l2 2.2h8.6A1.5 1.5 0 0 1 21 9.7v8.8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m5 12.5 4.5 4.5L19 7.5" />
    </svg>
  );
}

/**
 * The export window, centred over the page: where the files go (this project's folder, the default from Settings,
 * or downloads only), the one decision when peaks went over full scale (shared with the Inspector), and after
 * exporting the files, a zip of them and the folder.
 */
export function ExportDialog({ project, float, onFloat, open, onClose, anchorRef, onProject }: {
  project: Project; float: boolean; onFloat: (float: boolean) => void;
  open: boolean; onClose: () => void; anchorRef: RefObject<HTMLElement>; onProject?: (p: Project) => void;
}) {
  const { t } = useT();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ExportResult | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [picking, setPicking] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Links belong to the format they were made in: a format change (here or in the Inspector) clears them.
  useEffect(() => { setRes(null); }, [float]);
  useEffect(() => {
    if (!open) return;
    let live = true;
    api.settings().then((s) => { if (live) setSettings(s); }, () => { if (live) setSettings(null); });
    return () => { live = false; };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const back = anchorRef.current;
    box.current?.focus();
    return () => { back?.focus(); };
  }, [open, anchorRef]);

  useEffect(() => {
    if (!open || picking) return; // the folder picker handles its own keys
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key !== "Tab" || !box.current) return;
      const f = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1], at = document.activeElement;
      if (e.shiftKey && (at === first || at === box.current)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, picking, onClose]);

  if (!open) return null;

  const own = project.export_dir ?? null;
  const fallback = settings?.export_dir ?? null;
  const dest = own ?? fallback;

  async function go() {
    setBusy(true);
    try {
      const r = await api.exportFiles(project.id, float);
      setRes(r);
      if (r.save_error) toast.error(new Error(r.save_error));
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  }

  async function setDir(dir: string | null) {
    setPicking(false);
    try {
      const p = await api.setExportDir(project.id, dir);
      onProject?.(p);
      setRes(null);
    } catch (e) {
      toast.error(e);
    }
  }

  async function openFolder() {
    try {
      await api.openExports(project.id);
    } catch (e) {
      toast.error(e);
    }
  }

  return createPortal(
    <>
      <div className="dialog-backdrop export-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        <div ref={box} tabIndex={-1} className="export-modal" role="dialog" aria-modal="true" aria-label={t("export.title")}>
          <header className="exm-head">
            <div className="exm-title">
              <h2>{t("export.title")}</h2>
              <p className="muted" title={project.name}>{project.name}</p>
            </div>
            <button type="button" className="exm-close" aria-label={t("common.close")} onClick={onClose}>×</button>
          </header>

          <div className="exm-body">
            <section>
              <h3 className="exm-label">{t("export.destination")}</h3>
              <div className="exm-dest">
                <span className="exm-dest-ic"><FolderIcon /></span>
                <div className="exm-dest-text">
                  <div className="exm-dest-path mono" title={dest ?? undefined}>{dest ?? t("export.downloadOnly")}</div>
                  <div className="exm-dest-note muted">
                    {own ? (
                      <>
                        {t("export.destProject")}
                        {" · "}
                        <button type="button" className="linklike" onClick={() => void setDir(null)}>
                          {fallback ? t("export.useDefault") : t("export.useDownloadOnly")}
                        </button>
                      </>
                    ) : fallback ? t("export.destDefault") : settings && !settings.can_browse ? t("export.browseNotLocal") : t("export.destNone")}
                  </div>
                </div>
                {settings?.can_browse ? (
                  <button type="button" onClick={() => setPicking(true)}>{t("export.chooseFolder")}</button>
                ) : null}
              </div>
            </section>

            {peaksOver(project) ? (
              <section>
                <OutputChoice project={project} float={float} onFloat={onFloat} />
              </section>
            ) : null}

            {res ? (
              <section className="exm-result" aria-live="polite">
                <div className="exm-done">
                  <span className="exm-done-ic"><CheckIcon /></span>
                  <div className="exm-done-text">
                    <strong>{t("export.done")}</strong>
                    {res.saved?.length ? <span className="export-folder">{t("export.savedTo", { n: res.saved.length, folder: res.target ?? "" })}</span>
                      : res.folder ? <span className="muted export-folder">{t("export.savedIn", { folder: res.folder })}</span> : null}
                  </div>
                </div>
                {res.save_error ? <p className="export-folder err">{res.save_error}</p> : null}
                <ul className="exm-files">
                  {res.files.map((f) => (
                    <li key={f.url}>
                      <span className="exm-kind muted">{t(`export.${f.kind}`)}</span>
                      <a href={f.url} download={f.name}>{f.name}</a>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>

          <footer className="exm-foot">
            {res ? <a className="btn" href={res.zip_url} download>{t("export.downloadAll")}</a> : null}
            {res?.can_open_folder ? <button type="button" onClick={openFolder}>{t("export.openFolder")}</button> : null}
            <span className="spacer" />
            <button type="button" onClick={onClose}>{t("common.close")}</button>
            <button type="button" className="primary exm-go" disabled={busy} onClick={go}>{busy ? t("export.working") : t("export.go")}</button>
          </footer>
        </div>
      </div>
      {picking ? (
        <FolderPicker title={t("export.chooseFolderTitle")} initial={dest} confirmLabel={t("export.useThisFolder")}
                      onPick={(d) => void setDir(d)} onCancel={() => setPicking(false)} />
      ) : null}
    </>,
    document.body,
  );
}
