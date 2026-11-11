// web/src/i18n/index.ts
import { createContext, useContext } from "react";
import en from "./en.json";
import zh from "./zh-TW.json";

export const DICTS = { en, "zh-TW": zh } as const;
export type Lang = keyof typeof DICTS;
export type Vars = Record<string, string | number>;
export type T = (key: string, vars?: Vars) => string;

export function translate(lang: Lang, key: string, vars?: Vars): string {
  const d = DICTS[lang] as Record<string, string>;
  let s = d[key] ?? (DICTS.en as Record<string, string>)[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}

export function initialLang(): Lang {
  try {
    const s = localStorage.getItem("afix.lang");
    if (s === "en" || s === "zh-TW") return s;
  } catch {
    /* storage unavailable */
  }
  return "en";
}

export const I18nContext = createContext<{ lang: Lang; setLang: (l: Lang) => void; t: T }>({
  lang: "en",
  setLang: () => {},
  t: (k, v) => translate("en", k, v),
});

export const useT = () => useContext(I18nContext);
