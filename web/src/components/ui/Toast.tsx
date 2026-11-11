import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api";
import { useT } from "../../i18n";

type ToastItem = { id: number; kind: "info" | "error"; text: string };
type Api = { error(e: unknown): void; info(msg: string): void };

const ToastContext = createContext<Api>({ error: () => {}, info: () => {} });
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useT();
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => setItems((l) => l.filter((x) => x.id !== id)), []);
  const push = useCallback((kind: ToastItem["kind"], text: string) => {
    const id = ++seq.current;
    setItems((l) => [...l, { id, kind, text }]);
    if (kind === "info") setTimeout(() => dismiss(id), 5000);
  }, [dismiss]);

  const api = useMemo<Api>(() => ({
    info: (msg) => push("info", msg),
    error: (e) => {
      let text: string;
      if (e instanceof ApiError) {
        const k = "error." + e.code;
        text = t(k) !== k ? t(k) : e.message;
      } else if (e instanceof Error) text = e.message;
      else text = String(e);
      push("error", text);
    },
  }), [push, t]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts">
        {items.map((x) => (
          <div key={x.id} className={"toast " + x.kind} role={x.kind === "error" ? "alert" : "status"}>
            <span>{x.text}</span>
            {x.kind === "error" && (
              <button type="button" className="toast-close" aria-label={t("common.close")} onClick={() => dismiss(x.id)}>✕</button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
