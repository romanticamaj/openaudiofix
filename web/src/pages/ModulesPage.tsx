import { useCallback, useEffect, useState } from "react";
import { api, notifyModulesChanged, type ApiError, type ModelInfo, type ModuleInfo } from "../api";
import { ErrorBox } from "../components/ErrorBox";
import { GpuModulePanel } from "../components/GpuModulePanel";
import { Topbar } from "../components/shell/Topbar";
import { Section, SettingRow } from "../components/ui/Settings";
import { useToast } from "../components/ui/Toast";
import { fmtBytes } from "../format";
import { useT } from "../i18n";
import { href } from "../router";
import { gpuSummary } from "../status";

const ORDER = ["glitch", "declip", "denoise", "speech"];
const rank = (id: string) => (ORDER.includes(id) ? ORDER.indexOf(id) : ORDER.length);

/** Repair modules (CPU built-ins, the GPU module with its install panel) and the models they download. */
export function ModulesPage() {
  const { t } = useT();
  const toast = useToast();
  const [mods, setMods] = useState<ModuleInfo[]>([]);
  const [live, setLive] = useState<ModuleInfo | null>(null); // latest GPU state from the embedded panel
  const [loadErr, setLoadErr] = useState<ApiError | null>(null);
  const [models, setModels] = useState<{ dir: string; models: ModelInfo[]; can_open_folder: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const loadModels = useCallback(() => api.models().then((r) => setModels(Array.isArray(r?.models) ? r : null), () => setModels(null)), []);
  const onState = useCallback((m: ModuleInfo) => {
    setLive((prev) => {
      if (prev?.state !== m.state) void loadModels(); // an install or removal changes the GPU models
      return m;
    });
    notifyModulesChanged(m);
  }, [loadModels]);

  useEffect(() => {
    let alive = true;
    api.modules().then(
      (r) => { if (alive) { setLoadErr(null); setMods([...r.modules].sort((a, b) => rank(a.id) - rank(b.id))); } },
      (e) => { if (alive) setLoadErr(e as ApiError); },
    );
    void loadModels();
    return () => { alive = false; };
  }, [loadModels]);

  async function act(id: string, f: () => Promise<ModelInfo>) {
    setBusy(id);
    try {
      const m = await f();
      setModels((cur) => cur && { ...cur, models: cur.models.map((x) => (x.id === m.id ? m : x)) });
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(null);
    }
  }

  const cpu = mods.filter((m) => m.device !== "gpu"), gpu = mods.filter((m) => m.device === "gpu");
  const card = (m: ModuleInfo) => {
    const isGpu = m.device === "gpu";
    const s = gpuSummary(isGpu ? (live ?? m) : undefined);
    return (
      <section key={m.id} className="card module-card">
        <header className="module-head">
          <span className="module-chip" style={{ background: `var(--fix-${m.id})` }} />
          <h3>{t(`module.${m.id}`)}</h3>
          <span className="tag">{t(isGpu ? "modules.device.gpu" : "modules.device.cpu")}</span>
          {isGpu
            ? <span className={`badge ${s.tone === "idle" ? "" : s.tone}`}><span className={`dot ${s.tone}`} />{t(s.key, s.vars)}</span>
            : <span className="badge ok">{t("modules.builtin")}</span>}
        </header>
        <p className="muted">{t(`module.${m.id}.desc`)}</p>
        {isGpu ? <GpuModulePanel onState={onState} /> : null}
      </section>
    );
  };

  return (
    <>
      <Topbar crumbs={[t("nav.modules")]} />
      <div className="page modules-page">
        <ErrorBox error={loadErr} />
        {cpu.length ? <h2 className="mod-group">{t("modules.groupCpu")}</h2> : null}
        {cpu.map(card)}
        {gpu.length ? <h2 className="mod-group">{t("modules.groupGpu")}</h2> : null}
        {gpu.map(card)}
        {models ? (
          <Section title={t("models.title")} description={t("models.desc")}>
            <SettingRow title={t("models.folder")} description={<span className="mono">{models.dir}</span>} action={
              <div className="set-actions">
                {models.can_open_folder ? (
                  <button type="button" onClick={() => void api.openSettingFolder("models").catch((e) => toast.error(e))}>{t("settings.open")}</button>
                ) : null}
                <a className="btn" href={href({ name: "settings", tab: "storage" })}>{t("settings.change")}</a>
              </div>
            } />
            {models.models.map((m) => (
              <SettingRow key={m.id} title={m.name}
                description={<>
                  {t("models.usedBy", { mods: m.used_by.map((u) => t(`module.${u}`)).join(", ") })}
                  {" · "}{m.license}
                  {m.versions && Object.keys(m.versions).length ? <> · <span className="mono">{Object.entries(m.versions).map(([k, v]) => `${k} ${v}`).join(", ")}</span></> : null}
                </>}
                action={
                  <div className="set-actions model-actions">
                    <span className={`badge ${m.state === "ready" ? "ok" : m.state === "corrupt" ? "fail" : ""}`}>
                      <span className={`dot ${m.state === "ready" ? "ok" : m.state === "corrupt" ? "fail" : "idle"}`} />
                      {t(`models.state.${m.state}`)}
                    </span>
                    <span className="mono muted model-size">
                      {m.state === "ready" || m.size_bytes ? fmtBytes(m.size_bytes) : t("models.about", { size: fmtBytes(m.expected_bytes) })}
                    </span>
                    {m.managed_by ? null : m.state === "ready" ? (
                      <button type="button" disabled={busy === m.id} onClick={() => void act(m.id, () => api.deleteModel(m.id))}>{t("models.delete")}</button>
                    ) : (
                      <button type="button" className="primary" disabled={busy === m.id} onClick={() => void act(m.id, () => api.downloadModel(m.id))}>
                        {busy === m.id ? t("models.downloading") : m.state === "corrupt" ? t("models.redownload") : t("models.download")}
                      </button>
                    )}
                  </div>
                }>
                {m.managed_by ? <div className="muted set-meta">{t("models.managedBy", { mod: t(`module.${m.managed_by}`) })}</div> : null}
                {!m.managed_by && m.state !== "ready" ? <div className="muted set-meta">{t("models.autoDownload")}</div> : null}
              </SettingRow>
            ))}
          </Section>
        ) : null}
      </div>
    </>
  );
}
