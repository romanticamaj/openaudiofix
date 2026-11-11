import { type ReactNode, type RefObject, useEffect, useRef } from "react";

export function Popover({ open, onClose, anchorRef, children }: {
  open: boolean; onClose: () => void; anchorRef: RefObject<HTMLElement>; children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close.current(); };
    const onDown = (e: Event) => {
      const n = e.target as Node;
      if (panel.current?.contains(n) || anchorRef.current?.contains(n)) return;
      close.current();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open, anchorRef]);

  if (!open) return null;
  return <div className="popover" ref={panel}>{children}</div>;
}
