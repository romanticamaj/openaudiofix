import { useEffect, useRef, useState } from "react";
import { cssVar, overview } from "../Timeline";
import { clampView, pan, type View } from "../../timelineMath";

const H = 40;

/**
 * Whole-file overview with a draggable window showing the timeline's visible span.
 * A click puts the playhead exactly on the clicked time and centres the window there as far as the file allows;
 * a drag keeps the playhead at the window's centre. Either way playback does not pull the window back.
 */
export function Minimap({ projectId, src, rev = 0, duration, view, onView, onSeek }: {
  projectId: string; src: string; rev?: number; duration: number; view: View; onView: (v: View) => void; onSeek?: (t: number) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(800);
  const [peaks, setPeaks] = useState<number[]>([]);
  const drag = useRef<{ x: number; v: View; w: number; moved: boolean } | null>(null);

  useEffect(() => {
    const ro = new ResizeObserver((e) => setWidth(Math.max(100, Math.round(e[0].contentRect.width))));
    if (box.current) ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    let live = true;
    overview(projectId, src, rev).then((p) => { if (live) setPeaks(p); }, () => { if (live) setPeaks([]); });
    return () => { live = false; };
  }, [projectId, src, rev]);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = H * dpr;
    const g = c.getContext("2d");
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, width, H);
    const n = peaks.length / 2;
    if (!n) return;
    g.fillStyle = cssVar("--text-faint");
    const mid = H / 2;
    for (let x = 0; x < width; x++) {
      const a = Math.floor((x / width) * n), b = Math.max(a + 1, Math.floor(((x + 1) / width) * n));
      let mx = -1, mn = 1;
      for (let i = a; i < b && i < n; i++) { mx = Math.max(mx, peaks[2 * i]); mn = Math.min(mn, peaks[2 * i + 1]); }
      const y0 = mid - mx * mid, y1 = mid - mn * mid;
      g.fillRect(x, y0, 1, Math.max(1, y1 - y0));
    }
  }, [peaks, width]);

  const rect = () => box.current?.getBoundingClientRect() ?? { left: 0, width };
  const centreOn = (at: number) => clampView({ t0: at - (view.t1 - view.t0) / 2, t1: at + (view.t1 - view.t0) / 2 }, duration);
  const go = (v: View) => { onView(v); onSeek?.((v.t0 + v.t1) / 2); };
  const clickAt = (at: number) => { onView(centreOn(at)); onSeek?.(at); };
  const pct = (t: number) => (duration > 0 ? (t / duration) * 100 : 0);

  return (
    <div
      ref={box}
      className="minimap"
      onPointerDown={(e) => {
        const r = rect();
        const w = r.width || width;
        if ((e.target as Element).closest(".mini-win")) {
          drag.current = { x: e.clientX, v: view, w, moved: false };
          (e.target as Element).setPointerCapture?.(e.pointerId);
          return;
        }
        clickAt(((e.clientX - r.left) / w) * duration);
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        if (Math.abs(e.clientX - d.x) > 3) d.moved = true;
        if (d.moved) go(pan(d.v, ((e.clientX - d.x) / d.w) * duration, duration));
      }}
      onPointerUp={(e) => {
        const d = drag.current;
        drag.current = null;
        if (d && !d.moved) clickAt(((e.clientX - rect().left) / d.w) * duration); // a click on the window itself
      }}
      onPointerCancel={() => { drag.current = null; }}
    >
      <canvas ref={canvas} style={{ height: `${H}px` }} />
      <div className="mini-win" style={{ left: `${pct(view.t0)}%`, width: `${pct(view.t1 - view.t0)}%` }} />
    </div>
  );
}
