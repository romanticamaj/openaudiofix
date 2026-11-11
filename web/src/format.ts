// web/src/format.ts
export function fmtTime(t: number): string {
  const s = Math.max(0, t);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s - h * 3600 - m * 60;
  const ss = sec.toFixed(1).padStart(4, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function fmtBytes(n: number): string {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  if (i === 1 && n < 1e4) v = n / 1024;
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}

export function fmtDate(epoch: number, lang: string): string {
  return new Date(epoch * 1000).toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });
}

export function fmtRelative(epoch: number, nowMs: number, lang: string): string {
  const diff = Math.max(0, nowMs / 1000 - epoch);
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  if (diff < 60) return rtf.format(-Math.floor(diff), "second");
  if (diff < 3600) return rtf.format(-Math.floor(diff / 60), "minute");
  if (diff < 86400) return rtf.format(-Math.floor(diff / 3600), "hour");
  if (diff < 30 * 86400) return rtf.format(-Math.floor(diff / 86400), "day");
  return fmtDate(epoch, lang);
}
