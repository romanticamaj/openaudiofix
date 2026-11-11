import { useEffect, useRef, useState } from "react";
import { api, ApiError, urls, type ModuleInfo } from "../api";
import { fmtBytes } from "../format";
import { useT } from "../i18n";
import { ErrorBox } from "./ErrorBox";

const TERMINAL = new Set(["not_installed", "blocked", "ready_to_download", "installed", "failed"]);
const RETRY_BASE_MS = 500;
const RETRY_MAX_MS = 8000;
const MAX_RETRIES = 8;

/**
 * The server replays the current state on every SSE connect, so a terminal state seen after a click may be left
 * over from before it. While an action is pending we only stop listening on a terminal state whose `updated`
 * is newer than the last state seen before the click (server clock vs server clock: no skew).
 * The server also ends the stream after a terminal state, so the stream is reopened (with backoff) whenever an
 * action is pending or the last state seen was not terminal (e.g. the page opened mid-download).
 */
export function GpuModulePanel({ onState }: { onState?: (m: ModuleInfo) => void }) {
  const { t } = useT();
  const [s, setS] = useState<ModuleInfo | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const es = useRef<EventSource | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSeen = useRef(0);
  const lastState = useRef<string | null>(null);
  const pendingBase = useRef<number | null>(null);
  const retries = useRef(0);
  const alive = useRef(true);
  const onStateRef = useRef(onState);
  onStateRef.current = onState;

  function stop() {
    es.current?.close();
    es.current = null;
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
  }

  function watch() {
    stop();
    if (!alive.current) return;
    const e = new EventSource(urls.installEvents);
    e.onmessage = (m) => {
      const st = JSON.parse(m.data) as ModuleInfo;
      const upd = st.updated ?? 0;
      const base = pendingBase.current;
      const progressed = upd > lastSeen.current || !TERMINAL.has(st.state);
      if (progressed) { retries.current = 0; if (upd > lastSeen.current) setErr(null); }
      lastSeen.current = Math.max(lastSeen.current, upd);
      lastState.current = st.state;
      setS(st);
      onStateRef.current?.(st);
      if (TERMINAL.has(st.state) && (base === null || upd > base)) {
        pendingBase.current = null;
        stop();
      }
    };
    e.onerror = () => {
      stop();
      const active = pendingBase.current !== null || !TERMINAL.has(lastState.current ?? "");
      if (!active || !alive.current) return;
      if (retries.current >= MAX_RETRIES) {
        pendingBase.current = null;
        setErr(new ApiError(0, "network", "network error"));
        return;
      }
      const delay = Math.min(RETRY_BASE_MS * 2 ** retries.current, RETRY_MAX_MS);
      retries.current += 1;
      timer.current = setTimeout(watch, delay);
    };
    es.current = e;
  }

  async function act(fn: () => Promise<unknown>) {
    setErr(null);
    pendingBase.current = lastSeen.current;
    retries.current = 0;
    try {
      await fn();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae);
      if (ae.code !== "busy") { pendingBase.current = null; return; } // busy: another step runs, keep following it
    }
    watch();
  }

  useEffect(() => {
    alive.current = true;
    watch(); // the first event is the current state
    return () => { alive.current = false; stop(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const d = s?.detail ?? {};
  // inside a Docker image the GPU stack comes with the image: nothing to install or remove
  const managed = d.managed === "image";
  let body: JSX.Element = <p>{t("common.loading")}</p>;
  if (s?.state === "checking") body = <p>{t("gpu.checking")}</p>;
  else if (s?.state === "not_installed") body = (<>
    <p>{t("module.needsGpu")}</p>
    <button className="primary" onClick={() => act(() => api.install(false))}>{t("gpu.check")}</button></>);
  else if (s?.state === "blocked") body = (<>
    <p><strong>{t("gpu.blocked")}</strong></p><p>{t(`gpu.blockedHint.${d.code}`)}</p>
    {d.error ? <details><summary>{t("progress.details")}</summary><pre>{d.error}</pre></details> : null}
    <button onClick={() => act(() => api.install(false))}>{t("gpu.checkAgain")}</button></>);
  else if (s?.state === "ready_to_download") body = (<>
    <p>{t("gpu.found", { gpu: d.gpu, vram: d.vram, driver: d.driver })}</p>
    <p>{d.local_image ? t("gpu.localPackages") : t("gpu.confirm", { size: fmtBytes(d.download_bytes ?? 0), free: fmtBytes(d.free_bytes ?? 0) })}</p>
    {!d.enough_space ? <p className="error">{t("gpu.notEnough")}</p> : null}
    <button className="primary" disabled={!d.enough_space} onClick={() => act(() => api.install(true))}>{t("gpu.download")}</button></>);
  else if (s?.state === "downloading") body = (<>
    <p>{d.phase === "weights" ? t("gpu.downloadingWeights")
      : d.phase === "installing" ? t("gpu.installingPackages")
        : t("gpu.downloading", { pct: d.pct ?? 0, done: fmtBytes(d.done_bytes ?? 0), total: fmtBytes(d.total_bytes ?? 0) })}</p>
    <div className="bar"><i style={{ width: `${d.pct ?? 0}%` }} /></div></>);
  else if (s?.state === "verifying") body = <p>{t("gpu.verifying")}</p>;
  else if (s?.state === "installed") body = (<>
    <p>{t("gpu.installed", { gpu: d.gpu ?? "GPU", speed: d.speed_x ?? "?" })}</p>
    <p className="row"><button onClick={() => act(api.verify)}>{t("gpu.retest")}</button>
      {!managed ? <button className="danger" onClick={() => act(api.uninstall)}>{t("gpu.remove")}</button> : null}</p></>);
  else if (s?.state === "failed") {
    // what to run again depends on where it failed: removal -> remove; a missing image or a failure before the
    // self-test -> the GPU check; otherwise just the self-test
    const again = s.step === "remove" ? { label: t("gpu.remove"), run: () => api.uninstall() }
      : (s.step === "download" || s.step === "precheck" || d.code === "packages_missing" || d.code === "interrupted")
        ? { label: t("gpu.checkAgain"), run: () => api.install(false) }
        : { label: t("gpu.retest"), run: () => api.verify() };
    body = (<>
      <p className="error">{t("gpu.failed", { step: t(`gpu.step.${s.step}`) })} {d.code ? t(`error.${d.code}`) : ""}</p>
      {d.error ? <details open><summary>{t("progress.details")}</summary><pre>{d.error}</pre></details> : null}
      <p className="row"><button className="primary" onClick={() => act(again.run)}>{again.label}</button>
        {s.step !== "remove" && !managed ? <button className="danger" onClick={() => act(api.uninstall)}>{t("gpu.remove")}</button> : null}</p></>);
  }

  return (
    <div className="gpu-panel">
      {body}
      <ErrorBox error={err} />
    </div>
  );
}
