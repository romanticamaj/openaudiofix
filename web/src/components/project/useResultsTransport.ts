import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, urls, type FixRow, type Project, type SourceRef } from "../../api";
import { isWholeFile } from "../../timelineMath";
import { Transport } from "../../transport";
import { useToast } from "../ui/Toast";

export type ResultsTransport = {
  tp: Transport;
  sources: SourceRef[];
  diffs: SourceRef[];
  finalId: string;
  diffId: string;
  setDiffId(id: string): void;
  fixes: FixRow[];
  /** The fixes without whole-file spans: the places next/prev jump to. */
  points: FixRow[];
  /** Jump to the next/previous fix and report the new selection through `onStep`. */
  next(): void;
  prev(): void;
};

/** True when a key event comes from somewhere that types text: never steal those keys. */
function isTyping(el: HTMLElement | null): boolean {
  if (!el) return false;
  const tag = el.tagName?.toLowerCase();
  if (tag === "input") return (el as HTMLInputElement).type !== "range";
  if (tag === "textarea" || tag === "select") return true;
  if (el.isContentEditable) return true;
  return !!el.closest?.('[contenteditable]:not([contenteditable="false"])');
}

/**
 * True when Space belongs to the transport: no control has focus, or the focused control sits inside a
 * `[data-transport-keys]` container (workspace toolbar, transport bar, fix list). Elsewhere (top bar,
 * menus, popovers, inspector) buttons and links keep their native Space activation.
 */
function spaceIsTransport(el: HTMLElement | null): boolean {
  const tag = el?.tagName?.toLowerCase();
  if (tag !== "button" && tag !== "a") return true;
  return !!el!.closest?.("[data-transport-keys]");
}

/**
 * The done-state transport: playable sources, the fix list, and the 1/2/3/space/arrow keys.
 * `onStep` receives the index into `points` of the fix the transport lands on after next/prev.
 */
export function useResultsTransport(project: Project, onStep?: (index: number | null) => void): ResultsTransport {
  const toast = useToast();
  const res = project.result ?? { preview_gain_db: 0, export_gain_db: 0, sources: [], diffs: [], counts: {}, notes: {}, byte_exact_possible: false };
  const sr = project.sr ?? 48000;
  const duration = project.duration_s ?? 0;
  const finalId = res.sources[res.sources.length - 1]?.id ?? "original";
  const cleaned = !!res.cleaned;
  const sources = res.sources.filter((s) => !cleaned || s.id === "original" || s.id === finalId);
  const diffs = res.diffs.filter((d) => d.gain_db != null && (!cleaned || d.id === "d_chain"));
  const [diffId, setDiffId] = useState(diffs[0]?.id ?? "d_chain");
  const [fixes, setFixes] = useState<FixRow[]>([]);

  // The transport is rebuilt when the cleanup flips (the playable sources change); subscribers re-attach
  // because every consumer subscribes inside an effect keyed on the transport instance.
  const last = useRef({ id: "", t: 0 });
  const tp = useMemo(() => {
    const x = new Transport((url) => { const a = new Audio(); a.preload = "metadata"; a.src = url; return a; });
    x.dur = duration;
    return x;
  }, [project.id, duration, cleaned]);

  useEffect(() => {
    tp.setSources([...sources, ...diffs].map((s) => ({ id: s.id, url: urls.preview(project.id, s.id, res.rev) })), finalId);
    if (last.current.id === project.id) tp.seek(last.current.t);
    return () => { last.current = { id: project.id, t: tp.t }; tp.destroy(); };
  }, [tp]); // eslint-disable-line react-hooks/exhaustive-deps

  const toastRef = useRef(toast);
  toastRef.current = toast;
  useEffect(() => {
    let live = true;
    api.fixes(project.id).then((r) => { if (live) setFixes(r.fixes); }, (e) => { if (live) toastRef.current.error(e); });
    return () => { live = false; };
  }, [project.id]);

  // A whole-file span (denoise) is not a place to jump to: keep it out of next/prev and the counter.
  const points = useMemo(() => fixes.filter((f) => !isWholeFile(f, sr, duration)), [fixes, sr, duration]);
  useEffect(() => { tp.setFixes(points.map((f) => f.seconds)); }, [tp, points]);

  const stepRef = useRef(onStep);
  stepRef.current = onStep;
  const pointsRef = useRef(points);
  pointsRef.current = points;
  const sync = useCallback(() => {
    const s = tp.fixAt(tp.fixIndex() - 1);
    const i = s == null ? -1 : pointsRef.current.findIndex((p) => p.seconds === s);
    stepRef.current?.(i < 0 ? null : i);
  }, [tp]);
  const next = useCallback(() => { tp.next(); sync(); }, [tp, sync]);
  const prev = useCallback(() => { tp.prev(); sync(); }, [tp, sync]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (isTyping(el)) return;
      if (e.key === "1") { e.preventDefault(); tp.select("original"); }
      else if (e.key === "2") { e.preventDefault(); tp.select(finalId); }
      else if (e.key === "3") { e.preventDefault(); tp.select(diffId); }
      else if (e.key === " ") {
        if (!spaceIsTransport(el)) return; // the focused control handles its own activation
        e.preventDefault();
        if (!e.repeat) tp.toggle();
      }
      else if (e.key === "ArrowRight") { e.preventDefault(); next(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); prev(); }
    };
    // A focused button activates on Space keyup: suppress that inside the transport areas, or the
    // button's own click (e.g. Play) would run on top of the toggle.
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key !== " " || e.ctrlKey || e.metaKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (isTyping(el)) return;
      const tag = el?.tagName?.toLowerCase();
      if ((tag === "button" || tag === "a") && spaceIsTransport(el)) e.preventDefault();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("keyup", onKeyUp);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("keyup", onKeyUp); };
  }, [tp, finalId, diffId, next, prev]);

  return { tp, sources, diffs, finalId, diffId, setDiffId, fixes, points, next, prev };
}
