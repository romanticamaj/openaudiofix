import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type MenuItem = { label: string; onSelect: () => void; danger?: boolean; disabled?: boolean; keepOpen?: boolean };

export function Menu({ label, items, onOpenChange }: { label: string; items: MenuItem[]; onOpenChange?: (open: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const notify = useRef(onOpenChange);
  notify.current = onOpenChange;
  useEffect(() => { notify.current?.(open); }, [open]);
  const root = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; right: number; anchorTop: number } | null>(null);
  const [up, setUp] = useState(false);

  // After the list renders, flip it above the trigger when it would run past the viewport bottom.
  useLayoutEffect(() => {
    if (!open || !pos || !list.current) { setUp(false); return; }
    setUp(pos.top + list.current.offsetHeight > window.innerHeight);
  }, [open, pos]);

  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); btn.current?.focus(); }
    };
    const onAway = () => setOpen(false);
    const onDown = (e: Event) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("scroll", onAway, true);
    window.addEventListener("resize", onAway);
    return () => {
      window.removeEventListener("scroll", onAway, true);
      window.removeEventListener("resize", onAway);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <span className="menu" ref={root}>
      <button
        ref={btn}
        type="button"
        className="menu-btn"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          const r = btn.current?.getBoundingClientRect();
          if (r) setPos({ top: r.bottom + 4, right: window.innerWidth - r.right, anchorTop: r.top });
          setOpen((o) => !o);
        }}
      >
        ⋯
      </button>
      {open && (
        <div
          ref={list}
          className="menu-list"
          role="menu"
          style={!pos ? undefined
            : up ? { position: "fixed", top: "auto", bottom: window.innerHeight - (pos.anchorTop - 4), right: pos.right }
            : { position: "fixed", top: pos.top, right: pos.right }}
          onClick={(e) => e.stopPropagation()}>
          {items.map((it, i) => (
            <button
              key={i}
              type="button"
              role="menuitem"
              className={"menu-item" + (it.danger ? " danger" : "")}
              disabled={it.disabled}
              onClick={(e) => { e.stopPropagation(); if (!it.keepOpen) setOpen(false); it.onSelect(); }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
