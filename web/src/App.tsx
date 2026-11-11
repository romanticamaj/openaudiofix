import { useCallback, useEffect, useMemo, useState } from "react";
import { I18nContext, initialLang, translate, type Lang } from "./i18n";
import { useHashRoute } from "./router";
import { ToastProvider } from "./components/ui/Toast";
import { HealthBanner } from "./components/shell/HealthBanner";
import { ShellContext } from "./components/shell/ShellContext";
import { Sidebar } from "./components/shell/Sidebar";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ModulesPage } from "./pages/ModulesPage";
import { SettingsPage } from "./pages/SettingsPage";

export default function App() {
  const [lang, setLangState] = useState<Lang>(initialLang);
  const setLang = (l: Lang) => {
    setLangState(l);
    try { localStorage.setItem("afix.lang", l); } catch { /* ignore */ }
  };
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const t = useCallback((k: string, v?: Record<string, string | number>) => translate(lang, k, v), [lang]);
  const route = useHashRoute();
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => {
    const close = () => setNavOpen(false);
    addEventListener("hashchange", close);
    return () => removeEventListener("hashchange", close);
  }, []);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setNavOpen(false); };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [navOpen]);
  const shell = useMemo(() => ({ openNav: () => setNavOpen(true) }), []);

  let page;
  switch (route.name) {
    case "project": page = <ProjectPage id={route.id} />; break;
    case "modules": page = <ModulesPage />; break;
    case "settings": page = <SettingsPage tab={route.tab ?? "general"} />; break;
    default: page = <ProjectsPage />;
  }
  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      <ToastProvider>
        <ShellContext.Provider value={shell}>
          <div className="shell">
            <Sidebar route={route} open={navOpen} onClose={() => setNavOpen(false)} />
            {navOpen && <div className="nav-backdrop" data-testid="nav-backdrop" onClick={() => setNavOpen(false)} />}
            <div className="main">
              <HealthBanner />
              {page}
            </div>
          </div>
        </ShellContext.Provider>
      </ToastProvider>
    </I18nContext.Provider>
  );
}
