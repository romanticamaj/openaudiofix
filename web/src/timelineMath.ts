export type View = { t0: number; t1: number };

export function clampView(v: View, dur: number, minSpan = 0.05): View {
  const span = Math.min(Math.max(v.t1 - v.t0, minSpan), dur);
  const t0 = Math.min(Math.max(v.t0, 0), dur - span);
  return { t0, t1: t0 + span };
}

export function zoomAround(v: View, at: number, factor: number, dur: number): View {
  return clampView({ t0: at - (at - v.t0) * factor, t1: at + (v.t1 - at) * factor }, dur);
}

export function pan(v: View, dt: number, dur: number): View {
  return clampView({ t0: v.t0 + dt, t1: v.t1 + dt }, dur);
}

export const timeToX = (t: number, v: View, w: number) => ((t - v.t0) / (v.t1 - v.t0)) * w;
export const xToTime = (x: number, v: View, w: number) => v.t0 + (x / w) * (v.t1 - v.t0);

export function sliceOverview(peaks: number[], dur: number, v: View): number[] {
  const B = peaks.length / 2;
  const a = Math.max(0, Math.floor((v.t0 / dur) * B));
  const b = Math.min(B, Math.ceil((v.t1 / dur) * B));
  return peaks.slice(a * 2, b * 2);
}

export function follow(v: View, t: number, dur: number): View {
  if (t >= v.t0 && t <= v.t1) return v;
  const span = v.t1 - v.t0;
  return clampView({ t0: t - span * 0.1, t1: t - span * 0.1 + span }, dur);
}

/** A fix spanning (nearly) the whole file, as the denoise module reports: not a place to jump to. */
export function isWholeFile(f: { start: number; end: number }, sr: number, dur: number): boolean {
  return dur > 0 && (f.end - f.start) / sr >= 0.9 * dur;
}
