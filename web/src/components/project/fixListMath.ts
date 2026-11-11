import type { FixRow } from "../../api";

/** Above this many rows the fix list renders only a window of rows. Measured: a few thousand plain buttons stay smooth. */
export const VIRTUAL_THRESHOLD = 2000;
/** Fixed row height in px; the virtual window and the CSS both rely on it. */
export const ROW_H = 32;

const ORDER = ["glitch", "declip", "denoise", "speech"];

export function filterFixes(fixes: FixRow[], kind: string | "all"): FixRow[] {
  return kind === "all" ? fixes : fixes.filter((f) => f.module === kind);
}

/** The modules present, glitch → declip → denoise → speech first, then anything unknown in first-seen order. */
export function fixKinds(fixes: FixRow[]): string[] {
  const seen = new Set(fixes.map((f) => f.module));
  return [...ORDER.filter((m) => seen.has(m)), ...[...seen].filter((m) => !ORDER.includes(m))];
}

/** Half-open row range [first, last) to render for a scroll position, with `overscan` rows either side. */
export function visibleRange(scrollTop: number, viewport: number, rowH: number, total: number, overscan = 8): [number, number] {
  const first = Math.floor(Math.max(0, scrollTop) / rowH);
  const last = Math.ceil((Math.max(0, scrollTop) + viewport) / rowH);
  return [Math.max(0, Math.min(total, first - overscan)), Math.min(total, last + overscan)];
}
