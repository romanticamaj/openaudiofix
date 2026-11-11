import { useCallback, useEffect, useRef, useState } from "react";
import { api, type FolderListing } from "../api";
import { useT } from "../i18n";
import { useToast } from "./ui/Toast";

/** A place's short name: the last part of its path, or the whole of a drive or /. */
export function placeName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : path;
}

/**
 * Choose a folder on the server's computer: start from a common place, walk the folders (files are never listed),
 * or paste a path (network paths too), make a new folder, pick. The server confines a container to its mounts.
 * A path that does not exist yet opens at its nearest existing parent.
 */
export function FolderPicker({ title, initial, onPick, onCancel, confirmLabel }: {
  title: string; initial?: string | null; onPick: (path: string) => void; onCancel: () => void; confirmLabel?: string;
}) {
  const { t } = useT();
  const toast = useToast();
  const [list, setList] = useState<FolderListing | null>(null);
  const [typed, setTyped] = useState("");
  const [newName, setNewName] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const show = useCallback((l: FolderListing) => { setList(l); setTyped(l.path ?? ""); }, []);
  const load = useCallback(async (path: string) => {
    setBusy(true);
    try {
      show(await api.dirs(path));
    } catch (e) {
      toast.error(e);
      if (path) await api.dirs("").then(show, () => {});
    } finally {
      setBusy(false);
    }
  }, [toast, show]);

  useEffect(() => { void load(initial ?? ""); }, [load, initial]);
  useEffect(() => { box.current?.focus(); }, []);

  async function makeFolder() {
    if (!list?.path || !newName?.trim()) return;
    try {
      show(await api.mkdir(list.path, newName.trim()));
      setNewName(null);
    } catch (e) {
      toast.error(e);
    }
  }

  return (
    <div className="dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div ref={box} tabIndex={-1} className="dialog folder-picker" role="dialog" aria-modal="true" aria-label={title}
           onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onCancel(); } }}>
        <h2>{title}</h2>
        <div className="fp-roots" role="group" aria-label={t("folder.places")}>
          {(list?.roots ?? []).map((r) => (
            <button key={r} type="button" className="chip" title={r} onClick={() => void load(r)}>{placeName(r)}</button>
          ))}
        </div>
        <div className="fp-bar">
          <button type="button" aria-label={t("folder.up")} disabled={!list?.parent || busy}
                  onClick={() => list?.parent && void load(list.parent)}>↑</button>
          <input className="fp-path mono" type="text" spellCheck={false} value={typed} aria-label={t("folder.path")}
                 placeholder={t("folder.pathHint")} onChange={(e) => setTyped(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter" && typed.trim()) { e.preventDefault(); void load(typed.trim()); } }} />
          <button type="button" disabled={!typed.trim() || typed.trim() === list?.path || busy} onClick={() => void load(typed.trim())}>
            {t("folder.go")}
          </button>
        </div>
        <ul className="fp-list" aria-label={t("folder.folders")} aria-busy={busy}>
          {list?.dirs.length ? list.dirs.map((d) => (
            <li key={d.path}>
              <button type="button" onClick={() => void load(d.path)}>
                <span className="fp-ic" aria-hidden="true">📁</span>{d.name}
              </button>
            </li>
          )) : <li className="muted fp-empty">{list?.path ? t("folder.empty") : ""}</li>}
        </ul>
        {newName != null ? (
          <div className="fp-new">
            <input autoFocus type="text" value={newName} aria-label={t("folder.newName")} placeholder={t("folder.newName")}
                   onChange={(e) => setNewName(e.target.value)}
                   onKeyDown={(e) => {
                     if (e.key === "Enter") { e.preventDefault(); void makeFolder(); }
                     else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setNewName(null); }
                   }} />
            <button type="button" onClick={() => void makeFolder()} disabled={!newName.trim()}>{t("folder.create")}</button>
          </div>
        ) : null}
        <div className="dialog-actions fp-actions">
          <button type="button" className="fp-mk" disabled={!list?.path || !list.writable} onClick={() => setNewName("")}>
            {t("folder.new")}
          </button>
          <span className="spacer" />
          <button type="button" onClick={onCancel}>{t("common.cancel")}</button>
          <button type="button" className="primary" disabled={!list?.path || !list.writable || busy}
                  onClick={() => list?.path && onPick(list.path)}>
            {confirmLabel ?? t("folder.pick")}
          </button>
        </div>
        {list?.path && !list.writable ? <p className="muted">{t("folder.readOnly")}</p> : null}
      </div>
    </div>
  );
}
