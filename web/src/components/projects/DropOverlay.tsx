import { useEffect, useRef, useState } from "react";
import { useT } from "../../i18n";

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");

/** Page-wide file drop target. Listens on window; the depth counter stops flicker over child elements. */
export function DropOverlay({ onFile }: { onFile: (f: File) => void }) {
  const { t } = useT();
  const [show, setShow] = useState(false);
  const depth = useRef(0);
  const cb = useRef(onFile);
  cb.current = onFile;

  useEffect(() => {
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current++;
      setShow(true);
    };
    const over = (e: DragEvent) => { if (hasFiles(e)) e.preventDefault(); };
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setShow(false);
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      setShow(false);
      const f = e.dataTransfer?.files?.[0];
      if (f) cb.current(f);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, []);

  if (!show) return null;
  return <div className="drop-overlay" aria-hidden="true"><strong>{t("projects.dropHere")}</strong></div>;
}
