import { useEffect, useRef, useState } from "react";
import { useT } from "../../i18n";

export function RenameDialog({ initial, onSave, onCancel }: {
  initial: string; onSave: (name: string) => void; onCancel: () => void;
}) {
  const { t } = useT();
  const [value, setValue] = useState(initial);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); input.current?.select(); }, []);

  const save = () => {
    const name = value.trim();
    if (name) onSave(name);
  };
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={t("rename.title")}>
        <h2>{t("rename.title")}</h2>
        <input
          ref={input}
          className="rename-input"
          type="text"
          value={value}
          aria-label={t("rename.title")}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); save(); }
            else if (e.key === "Escape") { e.preventDefault(); onCancel(); }
          }}
        />
        <div className="dialog-actions">
          <button type="button" onClick={onCancel}>{t("common.cancel")}</button>
          <button type="button" className="primary" onClick={save} disabled={!value.trim()}>{t("rename.save")}</button>
        </div>
      </div>
    </div>
  );
}
