import { useId } from "react";
import type { Project } from "../../api";
import { useT } from "../../i18n";

const DEPTH: Record<string, string> = { PCM_16: "16-bit", PCM_24: "24-bit", PCM_32: "32-bit", FLOAT: "32-bit float" };

/** True when the repaired audio peaks over full scale, the one case where the export has a real choice. */
export const peaksOver = (p: Project) => p.state === "done" && (p.result?.export_gain_db ?? 0) < 0;

/**
 * The one export decision: repaired peaks over full scale cannot keep both the level and the bit depth. Anywhere else
 * there is nothing to decide, so nothing is shown. Render it only when peaksOver(project).
 */
export function OutputChoice({ project: p, float, onFloat }: {
  project: Project; float: boolean; onFloat: (float: boolean) => void;
}) {
  const { t } = useT();
  const group = useId(); // this choice shows in two places at once; each needs its own radio group
  const depth = DEPTH[p.subtype ?? ""] ?? "24-bit";
  const whole = (p.modules ?? []).includes("denoise");
  const db = Math.abs(p.result?.export_gain_db ?? 0).toFixed(1);
  const card = (value: boolean, title: string, desc: string) => (
    <label className={float === value ? "fmt-card on" : "fmt-card"}>
      <input type="radio" name={group} checked={float === value} onChange={() => onFloat(value)} />
      <span className="fmt-text">
        <span className="fmt-title">{title}</span>
        <span className="fmt-desc">{desc}</span>
      </span>
    </label>
  );
  return (
    <div className="out-choice">
      <div className="out-note wait">
        <strong>{t("output.over.title", { db })}</strong>
        <span>{t("output.over.desc")}</span>
      </div>
      <div className="fmt-grid">
        {card(true, t("output.keepLevel"), t(whole ? "output.keepLevelWholeDesc" : "output.keepLevelDesc"))}
        {card(false, t("output.keepDepth", { depth }), t(whole ? "output.keepDepthWholeDesc" : "output.keepDepthDesc", { db }))}
      </div>
    </div>
  );
}
