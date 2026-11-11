// web/src/theme.ts — theme preference (dark | light | system), persisted in localStorage.
export type ThemePref = "dark" | "light" | "system";

const KEY = "afix.theme";
const VALID: readonly string[] = ["dark", "light", "system"];

export function loadTheme(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    if (v && VALID.includes(v)) return v as ThemePref;
  } catch { /* storage blocked: use default */ }
  return "dark";
}

export function saveTheme(p: ThemePref): void {
  try { localStorage.setItem(KEY, p); } catch { /* ignore */ }
}

export function applyTheme(p: ThemePref): void {
  document.documentElement.dataset.theme = p;
}
