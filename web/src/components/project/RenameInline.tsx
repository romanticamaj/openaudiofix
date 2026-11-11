import { useEffect, useRef, useState } from "react";
import { useT } from "../../i18n";

/** The project name in the top bar; click to edit in place. `editing` may be controlled (the ⋯ menu opens it). */
export function RenameInline({ name, onSave, editing: editingProp, onEditingChange }: {
  name: string;
  onSave: (name: string) => Promise<unknown>;
  editing?: boolean;
  onEditingChange?: (editing: boolean) => void;
}) {
  const { t } = useT();
  const [own, setOwn] = useState(false);
  const editing = editingProp ?? own;
  const setEditing = (v: boolean) => { setOwn(v); onEditingChange?.(v); };
  const [pending, setPending] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) return;
    input.current?.focus();
    input.current?.select();
  }, [editing]);

  async function commit() {
    const next = (input.current?.value ?? "").trim();
    setEditing(false);
    if (!next || next === name) return;
    setPending(next);
    try { await onSave(next); } catch { /* the caller toasts; the old name comes back */ } finally { setPending(null); }
  }

  if (editing) {
    return (
      <input
        ref={input}
        className="rename-inline"
        aria-label={t("project.renameLabel")}
        defaultValue={name}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); void commit(); }
          else if (e.key === "Escape") { e.preventDefault(); setEditing(false); }
        }}
        onBlur={() => setEditing(false)}
      />
    );
  }
  const shown = pending ?? name;
  return (
    <button type="button" className="rename-name" title={shown} onClick={() => setEditing(true)}>{shown}</button>
  );
}
