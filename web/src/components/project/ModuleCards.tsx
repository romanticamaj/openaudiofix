import { useEffect, useState } from "react";
import type { ModuleInfo, ParamValue, Project } from "../../api";
import { useT } from "../../i18n";
import { href } from "../../router";
import { foundText, type ModuleChoice } from "./useModuleChoice";

const ICON: Record<string, string> = {
  glitch: "M1 8h4l1.5-4 3 8L11 8h4",
  declip: "M1 12c2 0 2-8 4-8h6c2 0 2 8 4 8",
  denoise: "M2 8h1M5 5v6M8 3v10M11 6v4M14 8h0",
  speech: "M1 8h2M4 5v6M6 3v10M8 6v4M10 8h5",
};

export function ModuleIcon({ id }: { id: string }) {
  return (
    <span className="mod-ic" style={{ color: `var(--fix-${id})` }} aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d={ICON[id] ?? "M3 8h10"} />
      </svg>
    </span>
  );
}

export function ModuleCards({ project, choice, editable }: { project: Project; choice: ModuleChoice; editable: boolean }) {
  const { t } = useT();
  return (
    <div className="mod-cards">
      {choice.mods.map((m) => {
        const usable = m.state === "installed";
        const name = t(`module.${m.id}`);
        const found = foundText(t, project, m.id);
        const values = editable ? choice.settings[m.id] : project.params?.[m.id];
        const showSettings = !!choice.on[m.id] && usable && !!values && Object.keys(values).length > 0;
        return (
          <div className="mod-card-wrap" key={m.id}>
          <div className="mod-card">
            <ModuleIcon id={m.id} />
            <div className="mod-text">
              <b>{name}</b>
              {found ? <small>{found}</small> : null}
              {t(`module.${m.id}.effect`) !== `module.${m.id}.effect` ? <small className="mod-effect">{t(`module.${m.id}.effect`)}</small> : null}
              {!usable && m.device === "gpu" ? (
                <small><a href={href({ name: "modules" })}>{t("module.install")}</a></small>
              ) : null}
            </div>
            <input
              type="checkbox"
              role="switch"
              className="switch"
              aria-label={name}
              title={t(`module.${m.id}.desc`)}
              checked={editable ? !!choice.on[m.id] && usable : !!choice.on[m.id]}
              disabled={!editable || !usable}
              onChange={() => choice.toggle(m.id)}
            />
          </div>
          {showSettings ? (
            <ModuleSettings module={m} values={values} editable={editable}
              onChange={(k, v) => choice.setSetting(m.id, k, v)} />
          ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** A module's settings, drawn from its params schema; labels come from module.<id>.param.<key>. */
function ModuleSettings({ module: m, values, editable, onChange }: {
  module: ModuleInfo; values: Record<string, ParamValue>; editable: boolean; onChange: (key: string, v: ParamValue) => void;
}) {
  const { t } = useT();
  const props = m.params?.properties ?? {};
  const hint = `module.${m.id}.settingsHint`;
  return (
    <div className="mod-settings" role="group" aria-label={t("module.settings", { name: t(`module.${m.id}`) })}>
      {Object.entries(props).map(([k, spec]) => {
        const label = t(`module.${m.id}.param.${k}`);
        const v = values[k] ?? spec.default;
        if (spec.enum) {
          return (
            <label key={k} className="mod-set">
              <span>{label}</span>
              <select value={String(v)} disabled={!editable} onChange={(e) => onChange(k, e.target.value)}>
                {spec.enum.map((o) => <option key={o} value={o}>{t(`module.${m.id}.param.${k}.${o}`)}</option>)}
              </select>
            </label>
          );
        }
        if (spec.type === "boolean") {
          return (
            <label key={k} className="mod-set">
              <input type="checkbox" checked={!!v} disabled={!editable} onChange={(e) => onChange(k, e.target.checked)} />
              <span>{label}</span>
            </label>
          );
        }
        return (
          <NumberSetting key={k} label={label} value={Number(v)} unit={k.endsWith("_ms") ? "ms" : ""}
            min={spec.minimum} max={spec.maximum} integer={spec.type === "integer"} disabled={!editable}
            onChange={(n) => onChange(k, n)} />
        );
      })}
      {t(hint) !== hint ? <small className="muted mod-set-hint">{t(hint)}</small> : null}
    </div>
  );
}

/** A number box that only reports values inside the schema's range; anything else snaps back on blur. */
function NumberSetting({ label, value, unit, min, max, integer, disabled, onChange }: {
  label: string; value: number; unit: string; min?: number; max?: number; integer: boolean; disabled: boolean;
  onChange: (n: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => { setText(String(value)); }, [value]);
  const valid = (s: string) => {
    const n = Number(s);
    return s.trim() !== "" && Number.isFinite(n) && (!integer || Number.isInteger(n))
      && n >= (min ?? -Infinity) && n <= (max ?? Infinity);
  };
  return (
    <label className="mod-set">
      <span>{label}</span>
      <input type="number" inputMode="numeric" value={text} min={min} max={max} step={integer ? 1 : "any"}
        disabled={disabled} aria-invalid={!valid(text)}
        onChange={(e) => { setText(e.target.value); if (valid(e.target.value)) onChange(Number(e.target.value)); }}
        onBlur={() => { if (!valid(text)) setText(String(value)); }} />
      {unit ? <span className="muted">{unit}</span> : null}
    </label>
  );
}
