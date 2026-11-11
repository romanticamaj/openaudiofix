import { useCallback, useEffect, useState } from "react";
import { api, type AppSettings } from "../api";
import { FolderPicker } from "../components/FolderPicker";
import { Topbar } from "../components/shell/Topbar";
import { Section, SettingRow } from "../components/ui/Settings";
import { useToast } from "../components/ui/Toast";
import { fmtBytes } from "../format";
import { useT, type Lang } from "../i18n";
import { href, SETTINGS_TABS, type SettingsTab } from "../router";
import { applyTheme, loadTheme, saveTheme, type ThemePref } from "../theme";

const THEMES: ThemePref[] = ["dark", "light", "system"];

export function SettingsPage({ tab = "general" }: { tab?: SettingsTab }) {
  const { t } = useT();
  return (
    <>
      <Topbar crumbs={[t("nav.settings")]} />
      <div className="page settings-page">
        <nav className="set-tabs" aria-label={t("nav.settings")}>
          {SETTINGS_TABS.map((k) => (
            <a key={k} href={href({ name: "settings", tab: k })} aria-current={tab === k ? "page" : undefined}>
              {t(`settings.tabs.${k}`)}
            </a>
          ))}
        </nav>
        {tab === "general" ? <GeneralTab /> : tab === "storage" ? <StorageTab /> : <AboutTab />}
      </div>
    </>
  );
}

function GeneralTab() {
  const { t, lang, setLang } = useT();
  const [theme, setTheme] = useState<ThemePref>(loadTheme);
  const pick = (p: ThemePref) => {
    setTheme(p);
    saveTheme(p);
    applyTheme(p);
  };
  return (
    <Section title={t("settings.tabs.general")}>
      <SettingRow title={t("settings.language")} description={t("settings.languageDesc")} action={
        <select aria-label="Language" value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
          <option value="en">English</option>
          <option value="zh-TW">繁體中文</option>
        </select>
      } />
      <SettingRow title={t("settings.appearance")} description={t("settings.appearanceDesc")} action={
        <div className="seg" role="group" aria-label={t("settings.appearance")}>
          {THEMES.map((p) => (
            <button key={p} type="button" aria-pressed={theme === p} className={theme === p ? "on" : undefined} onClick={() => pick(p)}>
              {t(`settings.theme.${p}`)}
            </button>
          ))}
        </div>
      } />
    </Section>
  );
}

type Picking = null | "export" | "data" | "models";

function StorageTab() {
  const { t } = useT();
  const toast = useToast();
  const [s, setS] = useState<AppSettings | null>(null);
  const [picking, setPicking] = useState<Picking>(null);
  const [restarting, setRestarting] = useState(false);

  const load = useCallback(async () => {
    try { setS(await api.settings()); } catch (e) { toast.error(e); }
  }, [toast]);
  useEffect(() => { void load(); }, [load]);

  // poll while a folder is being copied
  const copying = s?.move.state === "copying";
  useEffect(() => {
    if (!copying) return;
    const h = setInterval(() => { void load(); }, 700);
    return () => clearInterval(h);
  }, [copying, load]);

  async function run<T>(f: () => Promise<T>) {
    try { return await f(); } catch (e) { toast.error(e); return undefined; }
  }

  async function picked(path: string) {
    const what = picking;
    setPicking(null);
    if (what === "export") {
      const r = await run(() => api.saveSettings({ export_dir: path }));
      if (r) setS(r);
    } else if (what) {
      const r = await run(() => api.moveFolder(what, path));
      if (r) setS(r);
    }
  }

  async function restart() {
    setRestarting(true);
    if (!(await run(() => api.restart()))) { setRestarting(false); return; }
    // wait for the server to come back, then reload with the new folders
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try { await api.health(); location.reload(); return; } catch { /* still restarting */ }
    }
    setRestarting(false);
  }

  if (!s) return <Section title={t("settings.tabs.storage")}><p className="muted">{t("common.loading")}</p></Section>;

  const open = (what: "data" | "models" | "export") => s.can_open_folder ? (
    <button type="button" onClick={() => void run(() => api.openSettingFolder(what))}>{t("settings.open")}</button>
  ) : null;
  const pending = (what: "data" | "models") => {
    const cur = what === "data" ? s.data_dir : s.models_dir;
    const next = s.saved[what];
    return next && next !== cur ? next : null;
  };
  const folderRow = (what: "data" | "models", title: string, desc: string, bytes: number) => {
    const env = s.sources[what] === "env";
    const next = pending(what);
    const cur = what === "data" ? s.data_dir : s.models_dir;
    const def = s.defaults[what];
    return (
      <SettingRow title={title} description={desc} action={
        <div className="set-actions">
          {open(what)}
          {s.can_browse ? (
            <button type="button" disabled={env || copying} title={env ? t("settings.setByEnv", { v: what === "data" ? "AFIX_DATA" : "AFIX_MODELS" }) : undefined}
                    onClick={() => setPicking(what)}>{t("settings.change")}</button>
          ) : null}
        </div>
      }>
        <div className="set-path mono">{cur}</div>
        <div className="set-meta muted">
          {t("settings.used", { size: fmtBytes(bytes) })}
          {env ? ` · ${t("settings.setByEnv", { v: what === "data" ? "AFIX_DATA" : "AFIX_MODELS" })}` : ""}
        </div>
        {s.in_temp[what] ? <div className="set-warn">{t("settings.inTemp")}</div> : null}
        {cur !== def && !next ? (
          <div className="set-default muted">
            <span>{t("settings.defaultIs")} <span className="mono">{def}</span></span>
            {s.can_browse && !env ? (
              <button type="button" className="linklike" disabled={copying}
                      onClick={async () => { const r = await run(() => api.moveFolder(what, def)); if (r) setS(r); }}>
                {t("settings.moveToDefault")}
              </button>
            ) : null}
          </div>
        ) : null}
        {next ? <div className="set-pending">{t("settings.pendingMove", { folder: next })}</div> : null}
      </SettingRow>
    );
  };
  const m = s.move;
  const needsRestart = !!(pending("data") || pending("models")) && !copying;

  return (
    <>
      <Section title={t("settings.exportsTitle")} description={t("settings.exportsDesc")}>
        <SettingRow title={t("settings.defaultExport")} description={t("settings.defaultExportDesc")} action={
          <div className="set-actions">
            {s.export_dir ? open("export") : null}
            {s.export_dir ? (
              <button type="button" onClick={async () => { const r = await run(() => api.saveSettings({ export_dir: null })); if (r) setS(r); }}>
                {t("settings.downloadOnly")}
              </button>
            ) : null}
            {s.defaults.export && s.export_dir !== s.defaults.export ? (
              <button type="button" onClick={async () => { const r = await run(() => api.saveSettings({ export_dir: s.defaults.export })); if (r) setS(r); }}>
                {t("settings.useDefault")}
              </button>
            ) : null}
            {s.can_browse ? <button type="button" onClick={() => setPicking("export")}>{t("settings.choose")}</button> : null}
          </div>
        }>
          <div className="set-path mono">{s.export_dir ?? t("export.downloadOnly")}</div>
          {s.defaults.export && s.export_dir !== s.defaults.export ? (
            <div className="set-default muted"><span>{t("settings.defaultIs")} <span className="mono">{s.defaults.export}</span></span></div>
          ) : null}
        </SettingRow>
      </Section>
      <Section title={t("settings.foldersTitle")} description={t("settings.foldersDesc")}>
        {folderRow("data", t("settings.dataFolder"), t("settings.dataFolderDesc"), s.data_bytes)}
        {folderRow("models", t("settings.modelsFolder"), t("settings.modelsFolderDesc"), s.models_bytes)}
        {s.free_bytes != null ? <p className="muted set-free">{t("settings.free", { size: fmtBytes(s.free_bytes) })}</p> : null}
        {m.state === "copying" ? (
          <div className="set-progress" role="status">
            <div className="row">
              <span>{t("settings.copying", { folder: m.to ?? "" })}</span>
              <span className="mono">{fmtBytes(m.done_bytes ?? 0)}{m.total_bytes ? ` / ${fmtBytes(m.total_bytes)}` : ""}</span>
            </div>
            <div className="bar"><i style={{ width: `${m.total_bytes ? Math.min(100, ((m.done_bytes ?? 0) / m.total_bytes) * 100) : 2}%` }} /></div>
          </div>
        ) : null}
        {m.state === "failed" ? <p className="err">{t("settings.copyFailed", { error: m.error ?? "" })}</p> : null}
        {needsRestart ? (
          <div className="set-restart">
            <span>{t("settings.restartNeeded")}</span>
            {s.can_restart ? (
              <button type="button" className="primary" disabled={restarting} onClick={() => void restart()}>
                {restarting ? t("settings.restarting") : t("settings.restartNow")}
              </button>
            ) : <span className="muted">{t("settings.restartByHand")}</span>}
          </div>
        ) : null}
        {!s.can_browse ? <p className="muted">{t("settings.notLocal")}</p> : null}
        <p className="muted">{t("settings.cleanHint")}</p>
      </Section>
      {picking ? (
        <FolderPicker
          title={t(picking === "export" ? "settings.pickExport" : picking === "data" ? "settings.pickData" : "settings.pickModels")}
          initial={picking === "export" ? s.export_dir ?? s.defaults.export : s.defaults[picking]}
          confirmLabel={picking === "export" ? t("export.useThisFolder") : t("settings.moveHere")}
          onPick={(p) => void picked(p)} onCancel={() => setPicking(null)} />
      ) : null}
    </>
  );
}

function AboutTab() {
  const { t } = useT();
  return (
    <Section title={t("settings.about")}>
      <SettingRow title="OpenAudioFix" description="0.1.0 · MIT License" />
    </Section>
  );
}
