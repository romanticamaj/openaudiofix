import { useEffect, useRef, useState } from "react";
import { urls } from "../../api";

const W = 120, H = 28;

export function Sparkline({ projectId, enabled }: { projectId: string; enabled: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [peaks, setPeaks] = useState<number[] | null>(null);

  useEffect(() => {
    setPeaks(null);
    if (!enabled) return;
    let live = true;
    (async () => {
      try {
        const r = await fetch(urls.overviewPeaks(projectId, "original"));
        if (!r.ok) return;
        const d = await r.json();
        if (live && Array.isArray(d) && d.length >= 2) setPeaks(d);
      } catch { /* never show an error */ }
    })();
    return () => { live = false; };
  }, [projectId, enabled]);

  useEffect(() => {
    const c = ref.current;
    const g = c?.getContext("2d");
    if (!c || !g) return;
    g.clearRect(0, 0, W, H);
    g.fillStyle = getComputedStyle(c).getPropertyValue("--text-faint").trim() || "#888";
    const mid = H / 2;
    if (!peaks) { g.fillRect(0, mid, W, 1); return; }
    const n = Math.floor(peaks.length / 2);
    for (let x = 0; x < W; x++) {
      const a = Math.floor((x / W) * n), b = Math.max(a + 1, Math.floor(((x + 1) / W) * n));
      let hi = -1, lo = 1;
      for (let i = a; i < b && i < n; i++) {
        hi = Math.max(hi, peaks[2 * i], peaks[2 * i + 1]);
        lo = Math.min(lo, peaks[2 * i], peaks[2 * i + 1]);
      }
      if (hi < lo) continue;
      const y0 = mid - hi * mid, y1 = mid - lo * mid;
      g.fillRect(x, y0, 1, Math.max(1, y1 - y0));
    }
  }, [peaks]);

  return <canvas ref={ref} className="sparkline" width={W} height={H} data-state={peaks ? "wave" : "flat"} aria-hidden="true" />;
}
