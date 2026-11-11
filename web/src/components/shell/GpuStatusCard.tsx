import { useT } from "../../i18n";
import type { ModuleInfo } from "../../api";
import { gpuSummary } from "../../status";

export function GpuStatusCard({ module, onClick }: { module?: ModuleInfo; onClick?: () => void }) {
  const { t } = useT();
  const s = gpuSummary(module);
  const d = module?.detail ?? {};
  const showDetail = module?.state === "installed" && d.gpu;
  return (
    <a className="gpu-card" href="#/modules" onClick={onClick}>
      <span className={`dot ${s.tone}`} />
      <span className="gpu-card-text">
        <span>{t(s.key, s.vars)}</span>
        {showDetail && <span className="muted gpu-card-detail">{t("gpu.card.readyDetail", { gpu: d.gpu, speed: d.speed_x ?? "" })}</span>}
      </span>
    </a>
  );
}
