// web/src/router.ts
import { useEffect, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "project"; id: string }
  | { name: "modules" }
  | { name: "settings"; tab?: SettingsTab };

export const SETTINGS_TABS = ["general", "storage", "about"] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];

export function parseHash(hash: string): Route {
  if (hash === "#/modules") return { name: "modules" };
  if (hash === "#/settings") return { name: "settings" };
  const st = hash.match(/^#\/settings\/([a-z]+)$/);
  if (st && (SETTINGS_TABS as readonly string[]).includes(st[1])) return { name: "settings", tab: st[1] as SettingsTab };
  const m = hash.match(/^#\/p\/([a-z0-9]+)$/);
  return m ? { name: "project", id: m[1] } : { name: "home" };
}

export function href(r: Route): string {
  switch (r.name) {
    case "project": return `#/p/${r.id}`;
    case "modules": return "#/modules";
    case "settings": return r.tab && r.tab !== "general" ? `#/settings/${r.tab}` : "#/settings";
    default: return "#/";
  }
}

export function go(r: Route) {
  location.hash = href(r);
}

export function useHashRoute(): Route {
  const [r, setR] = useState(() => parseHash(location.hash));
  useEffect(() => {
    const f = () => setR(parseHash(location.hash));
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  return r;
}
