import { useState } from "react";
import type { Project } from "../../api";
import { fmtBytes, fmtDate, fmtRelative, fmtTime } from "../../format";
import { useT } from "../../i18n";
import { go } from "../../router";
import { fmtDuration, overall } from "../../progress";
import type { Sort, SortKey } from "../../projectFilter";
import { projectFixCount } from "../../status";
import { Badge } from "../ui/Badge";
import { Menu } from "../ui/Menu";
import { Sparkline } from "../ui/Sparkline";

export type Uploading = { name: string; pct: number };

function subline(p: Project): string {
  const parts: string[] = [];
  if (p.codec) parts.push(p.codec);
  if (p.sr) parts.push(`${+(p.sr / 1000).toFixed(1)} kHz`);
  if (p.channels) parts.push(`${p.channels} ch`);
  return parts.join(" · ");
}

function BusyBar({ p }: { p: Project }) {
  // The list carries the jobs of busy projects; without them (an older server) the bar is indeterminate.
  const known = (p.jobs ?? []).length > 0;
  const o = overall(p);
  return (
    <>
      <div className="bar thin" aria-hidden="true">
        <i className={known ? undefined : "indeterminate"} style={known ? { width: `${Math.max(2, o.pct)}%` } : undefined} />
      </div>
      {known ? (
        <span className="muted mono busy-pct">
          {Math.floor(o.pct)}%{o.remaining != null ? ` · ${fmtDuration(o.remaining)}` : ""}
        </span>
      ) : null}
    </>
  );
}

function RowMenu({ onRename, onDelete }: { onRename: () => void; onDelete: () => void }) {
  const { t } = useT();
  const [confirm, setConfirm] = useState(false);
  return (
    <Menu
      label={t("projects.actions")}
      onOpenChange={(open) => { if (!open) setConfirm(false); }}
      items={[
        { label: t("projects.rename"), onSelect: onRename },
        {
          label: confirm ? t("projects.confirmDelete") : t("projects.delete"),
          danger: true,
          keepOpen: !confirm,
          onSelect: () => { if (confirm) onDelete(); else setConfirm(true); },
        },
      ]}
    />
  );
}

function Th({ k, label, sort, onSort }: { k: SortKey; label: string; sort?: Sort; onSort?: (s: Sort) => void }) {
  if (!onSort || !sort) return <th>{label}</th>;
  const on = sort.key === k;
  // numbers and dates start biggest/newest first, names and states A to Z
  const first = k === "name" || k === "state" ? "asc" : "desc";
  const next = on ? (sort.dir === "asc" ? "desc" : "asc") : first;
  return (
    <th aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className={on ? "th-sort on" : "th-sort"} onClick={() => onSort({ key: k, dir: next })}>
        {label}<span className="th-arrow" aria-hidden="true">{on ? (sort.dir === "asc" ? "\u25B2" : "\u25BC") : "\u2195"}</span>
      </button>
    </th>
  );
}

export function ProjectTable({ projects, uploading, noMatch, onRename, onDelete, sort, onSort }: {
  projects: Project[]; uploading: Uploading | null; noMatch?: boolean;
  onRename: (p: Project) => void; onDelete: (p: Project) => void; sort?: Sort; onSort?: (s: Sort) => void;
}) {
  const { t, lang } = useT();
  const now = Date.now();
  const cols = 10;
  return (
    <div className="table-wrap">
      <table className="ptable">
        <thead>
          <tr>
            <Th k="name" label={t("projects.name")} sort={sort} onSort={onSort} />
            <th>{t("projects.col.waveform")}</th>
            <Th k="state" label={t("projects.state")} sort={sort} onSort={onSort} />
            <th>{t("projects.col.modules")}</th>
            <Th k="duration" label={t("projects.duration")} sort={sort} onSort={onSort} />
            <Th k="fixes" label={t("projects.col.fixes")} sort={sort} onSort={onSort} />
            <Th k="run" label={t("projects.col.run")} sort={sort} onSort={onSort} />
            <Th k="size" label={t("projects.col.size")} sort={sort} onSort={onSort} />
            <Th k="created" label={t("projects.created")} sort={sort} onSort={onSort} />
            <th aria-hidden="true" />
          </tr>
        </thead>
        <tbody>
          {uploading && (
            <tr className="uploading-row">
              <td><div className="pname" title={uploading.name}>{uploading.name}</div></td>
              <td colSpan={cols - 1}>
                <div className="bar thin"><i style={{ width: `${uploading.pct}%` }} /></div>
                <span className="muted">{t("projects.uploading", { pct: uploading.pct })}</span>
              </td>
            </tr>
          )}
          {noMatch && !uploading && (
            <tr><td colSpan={cols} className="muted">{t("projects.noMatch")}</td></tr>
          )}
          {projects.map((p) => (
            <tr
              key={p.id}
              className="prow"
              tabIndex={0}
              onClick={() => go({ name: "project", id: p.id })}
              onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) go({ name: "project", id: p.id }); }}
            >
              <td>
                <div className="pname" title={p.name}>{p.name}</div>
                <div className="psub">{subline(p)}</div>
              </td>
              <td><Sparkline projectId={p.id} enabled={p.state === "done"} /></td>
              <td>
                <Badge state={p.state} />
                {(p.state === "scanning" || p.state === "processing") && <BusyBar p={p} />}
              </td>
              <td>
                <div className="pmods">
                  {(p.modules ?? []).map((m) => (
                    <span key={m} className="pmod">
                      <span className="dot" style={{ background: `var(--fix-${m})` }} aria-hidden="true" />{t(`module.${m}`)}
                    </span>
                  ))}
                  {(p.modules ?? []).length ? null : <span className="muted">—</span>}
                </div>
              </td>
              <td className="mono">{p.duration_s != null ? fmtTime(p.duration_s) : "–"}</td>
              <td className="mono">{p.state === "done" ? projectFixCount(p) : "—"}</td>
              <td className="mono" title={p.run_s && p.duration_s ? t("inspector.speedValue", { x: (p.duration_s / p.run_s).toFixed(1) }) : undefined}>
                {p.run_s != null ? fmtDuration(p.run_s) : "—"}
              </td>
              <td className="mono">{p.size_bytes ? fmtBytes(p.size_bytes) : "—"}</td>
              <td title={fmtDate(p.created, lang)}>{fmtRelative(p.created, now, lang)}</td>
              <td className="pmenu" onClick={(e) => e.stopPropagation()}>
                <RowMenu onRename={() => onRename(p)} onDelete={() => onDelete(p)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
