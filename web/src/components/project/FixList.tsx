import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { FixRow } from "../../api";
import { fmtTime } from "../../format";
import { useT } from "../../i18n";
import { isWholeFile } from "../../timelineMath";
import { filterFixes, fixKinds, ROW_H, VIRTUAL_THRESHOLD, visibleRange } from "./fixListMath";

const DEFAULT_VIEWPORT = 640;

/** A fix's length: milliseconds for clicks and clips, seconds for the long spans speech removes. */
export function spanText(seconds: number): string {
  return seconds < 1 ? `${(seconds * 1000).toFixed(1)} ms` : `${seconds.toFixed(1)} s`;
}

/** The right-hand fix list: kind chips, one button per fix, virtual window above VIRTUAL_THRESHOLD rows. */
export function FixList({ fixes, selected, onPick, sr, duration }: {
  fixes: FixRow[];
  selected: FixRow | null;
  onPick: (seconds: number, row: FixRow) => void;
  sr: number;
  duration: number;
}) {
  const { t } = useT();
  const [kind, setKind] = useState("all");
  const kinds = useMemo(() => fixKinds(fixes), [fixes]);
  const active = kind === "all" || kinds.includes(kind) ? kind : "all";
  const filtered = useMemo(() => filterFixes(fixes, active), [fixes, active]);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of fixes) m.set(f.module, (m.get(f.module) ?? 0) + 1);
    return m;
  }, [fixes]);

  const virtual = filtered.length > VIRTUAL_THRESHOLD;
  const body = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState(0);
  const [viewport, setViewport] = useState(DEFAULT_VIEWPORT);

  const selIdx = selected ? filtered.indexOf(selected) : -1;
  const sel = useRef({ idx: -1, virtual: false });
  sel.current = { idx: selIdx, virtual };

  /** Bring the selected row into view. A hidden list (display:none tab panel) has no height: retry when shown. */
  const reveal = useCallback(() => {
    const el = body.current;
    const { idx, virtual: v } = sel.current;
    if (!el || idx < 0) return;
    const view = el.clientHeight;
    if (!view) return; // the measure below calls reveal again once the panel has a height
    if (v) {
      const top = idx * ROW_H, bottom = top + ROW_H;
      let st = el.scrollTop;
      if (top < st) st = top;
      else if (bottom > st + view) st = bottom - view;
      if (st !== el.scrollTop) el.scrollTop = st;
      setScroll(el.scrollTop);
    } else {
      el.querySelector<HTMLElement>('[aria-current="true"]')?.scrollIntoView?.({ block: "nearest" });
    }
  }, []);

  useLayoutEffect(() => {
    const el = body.current;
    if (!el) return;
    let lastH = -1;
    const measure = () => {
      const h = el.clientHeight;
      setViewport(h || DEFAULT_VIEWPORT);
      if (h && lastH <= 0) {
        // Just became visible (or first measure): the browser may have dropped the scroll position while hidden.
        setScroll(el.scrollTop);
        reveal();
      }
      lastH = h;
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [reveal]);

  // A new filter starts at the top.
  useEffect(() => {
    if (body.current) body.current.scrollTop = 0;
    setScroll(0);
  }, [active]);

  useEffect(reveal, [selIdx, virtual, reveal]);

  const [first, last] = virtual ? visibleRange(scroll, viewport, ROW_H, filtered.length) : [0, filtered.length];
  const rows: JSX.Element[] = [];
  for (let i = first; i < last; i++) {
    const f = filtered[i];
    const whole = isWholeFile(f, sr, duration);
    rows.push(
      <button
        type="button"
        key={`${f.module}-${f.start}-${f.channel}-${i}`}
        className="fx-row"
        aria-current={f === selected ? "true" : undefined}
        style={virtual ? { position: "absolute", top: i * ROW_H, left: 0, right: 0 } : undefined}
        onClick={() => onPick(whole ? 0 : f.seconds, f)}
      >
        <span className="dot" style={{ background: `var(--fix-${f.module})` }} aria-hidden="true" />
        <span className="fx-t mono">{whole ? t("results.wholeFile") : fmtTime(f.seconds)}</span>
        <span className="fx-m">
          {t(`module.${f.module}`)}{whole ? "" : ` · ${spanText((f.end - f.start) / sr)}`}
        </span>
        <span className="fx-play" aria-hidden="true">▶</span>
      </button>,
    );
  }

  return (
    <section className="fixes" data-transport-keys="">
      <div className="fixes-hd">
        <h3>{t("fixes.header", { n: filtered.length, total: fixes.length })}</h3>
        <div className="fixes-chips" role="group" aria-label={t("results.fixes")}>
          <button type="button" className="chip" aria-pressed={active === "all"} onClick={() => setKind("all")}>
            {t("results.filterAll")} <span className="chip-n">{fixes.length}</span>
          </button>
          {kinds.map((m) => (
            <button key={m} type="button" className="chip" aria-pressed={active === m} onClick={() => setKind(m)}>
              <span className="dot" style={{ background: `var(--fix-${m})` }} aria-hidden="true" />
              {t(`module.${m}`)} <span className="chip-n">{counts.get(m) ?? 0}</span>
            </button>
          ))}
        </div>
      </div>
      <div ref={body} className="fx-body" onScroll={virtual ? (e) => setScroll(e.currentTarget.scrollTop) : undefined}>
        {virtual ? <div className="fx-spacer" style={{ height: `${filtered.length * ROW_H}px` }}>{rows}</div> : rows}
      </div>
    </section>
  );
}
