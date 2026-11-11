import { useCallback, useEffect, useRef, useState } from "react";
import { urls, type FixRow } from "../api";
import { fmtTime } from "../format";
import type { Transport } from "../transport";
import { follow, isWholeFile, pan, sliceOverview, timeToX, xToTime, zoomAround, type View } from "../timelineMath";

const WAVE_H = 96, MARK_H = 8, SPEC_H = 160;
const MAX_API_SPAN = 120; // server MAX_PEAK_SPAN_S: wider views use the overview peaks
const COLORS: Record<string, string> = { glitch: "--fix-glitch", declip: "--fix-declip", denoise: "--fix-denoise", speech: "--fix-speech" };
const TICKS = 6;
const overviewCache = new Map<string, Promise<number[]>>();

export function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

/** Whole-file overview peaks for a source, fetched once and shared by the timeline and the minimap. */
export function overview(projectId: string, src: string, rev = 0) {
  const key = `${projectId}/${src}/${rev}`;
  let p = overviewCache.get(key);
  if (!p) {
    p = fetch(urls.overviewPeaks(projectId, src, rev)).then((r) => {
      if (!r.ok) throw new Error(String(r.status));
      return r.json() as Promise<number[]>;
    });
    p.catch(() => overviewCache.delete(key));
    overviewCache.set(key, p);
  }
  return p;
}

/** The big waveform. Its view is controlled so the minimap can share it. */
export function Timeline({ projectId, src, rev = 0, duration, sr, fixes, transport, view, onView }: {
  projectId: string; src: string; rev?: number; duration: number; sr: number; fixes: FixRow[]; transport: Transport;
  view: View; onView: (v: View) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const head = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [specUrl, setSpecUrl] = useState("");
  const drag = useRef<{ x: number; v: View; moved: boolean } | null>(null);
  const viewRef = useRef(view);
  viewRef.current = view;
  const onViewRef = useRef(onView);
  onViewRef.current = onView;

  useEffect(() => {
    const ro = new ResizeObserver((e) => setWidth(Math.max(200, Math.round(e[0].contentRect.width))));
    if (wrap.current) ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  // Waveform + spectrogram data for the current view (debounced; stale responses are dropped).
  useEffect(() => {
    let live = true;
    const h = setTimeout(async () => {
      try {
        if (view.t1 - view.t0 > MAX_API_SPAN) {
          const all = await overview(projectId, src, rev);
          if (live) setPeaks(sliceOverview(all, duration, view));
        } else {
          const r = await fetch(urls.peaks(projectId, src, view.t0, view.t1, width, rev));
          if (r.ok && live) setPeaks((await r.json()).peaks);
        }
      } catch {
        if (live) setPeaks([]);
      }
      if (live) setSpecUrl(urls.spectrogram(projectId, src, view.t0, view.t1, Math.min(width, 2000), SPEC_H, rev));
    }, 120);
    return () => { live = false; clearTimeout(h); };
  }, [projectId, src, rev, view, width, duration]);

  // Waveform and fix markers share one canvas; there is no DOM node per fix.
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = (WAVE_H + MARK_H) * dpr;
    c.style.height = `${WAVE_H + MARK_H}px`;
    const g = c.getContext("2d");
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, width, WAVE_H + MARK_H);
    const n = peaks.length / 2;
    g.fillStyle = cssVar("--text-muted");
    const mid = MARK_H + WAVE_H / 2;
    for (let x = 0; x < width && n; x++) {
      const a = Math.floor((x / width) * n), b = Math.max(a + 1, Math.floor(((x + 1) / width) * n));
      let mx = -1, mn = 1;
      for (let i = a; i < b && i < n; i++) { mx = Math.max(mx, peaks[2 * i]); mn = Math.min(mn, peaks[2 * i + 1]); }
      const y0 = mid - mx * (WAVE_H / 2), y1 = mid - mn * (WAVE_H / 2);
      g.fillRect(x, y0, 1, Math.max(1, y1 - y0));
    }
    const colour = new Map<string, string>();
    // Whole-file spans go first so they never cover glitch/declip markers.
    const ordered = [...fixes.filter((f) => isWholeFile(f, sr, duration)), ...fixes.filter((f) => !isWholeFile(f, sr, duration))];
    for (const f of ordered) {
      const s = f.start / sr, e = f.end / sr;
      if (e < view.t0 || s > view.t1) continue;
      const x = timeToX(s, view, width), w = Math.max(1, timeToX(e, view, width) - x);
      let col = colour.get(f.module);
      if (!col) { col = cssVar(COLORS[f.module] ?? "--accent"); colour.set(f.module, col); }
      g.fillStyle = col;
      g.fillRect(x, 0, w, MARK_H);
      g.globalAlpha = 0.18;
      g.fillRect(x, MARK_H, w, WAVE_H);
      g.globalAlpha = 1;
    }
  }, [peaks, fixes, view, width, sr, duration]);

  const placeHead = useCallback(() => {
    const el = head.current;
    if (!el) return;
    const x = timeToX(transport.t, viewRef.current, width);
    el.style.display = x < 0 || x > width ? "none" : "block";
    el.style.left = `${x}px`;
  }, [transport, width]);

  useEffect(() => transport.subscribe(() => {
    if (transport.playing) {
      const v = viewRef.current, nv = follow(v, transport.t, duration);
      if (nv !== v) { onViewRef.current(nv); viewRef.current = nv; } // later events in this frame build on it
    }
    placeHead();
  }), [transport, duration, placeHead]);

  useEffect(placeHead, [view, placeHead]);

  const localX = (e: { clientX: number }) => e.clientX - (wrap.current?.getBoundingClientRect().left ?? 0);

  // Native non-passive listener so the page does not scroll while zooming.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const v = viewRef.current;
      const nv = zoomAround(v, xToTime(localX(e), v, width), e.deltaY > 0 ? 1.25 : 0.8, duration);
      onViewRef.current(nv);
      viewRef.current = nv; // several wheel events can land before the next render
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [width, duration]);

  const span = view.t1 - view.t0;
  const ticks = Array.from({ length: TICKS }, (_, i) => view.t0 + (span * i) / (TICKS - 1));

  return (
    <div className="wavebox">
      <div className="ruler mono" aria-hidden="true">
        {ticks.map((x, i) => <span key={i}>{fmtTime(x)}</span>)}
      </div>
      <div
        ref={wrap}
        className="timeline"
        onPointerDown={(e) => { drag.current = { x: e.clientX, v: view, moved: false }; (e.target as Element).setPointerCapture?.(e.pointerId); }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const dx = e.clientX - d.x;
          if (Math.abs(dx) > 3) d.moved = true;
          if (d.moved) onView(pan(d.v, -(dx / width) * (d.v.t1 - d.v.t0), duration));
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (d && !d.moved) transport.seek(xToTime(localX(e), view, width));
        }}
        onPointerCancel={() => { drag.current = null; }}
      >
        <canvas ref={canvas} />
        {specUrl ? <img src={specUrl} alt="" draggable={false} /> : null}
        <div ref={head} className="playhead" />
      </div>
    </div>
  );
}
