import { Fragment, useContext, type ReactNode } from "react";
import { useT } from "../../i18n";
import { ShellContext } from "./ShellContext";

export function Topbar({ crumbs, badge, actions }: { crumbs: ReactNode[]; badge?: ReactNode; actions?: ReactNode }) {
  const { t } = useT();
  const { openNav } = useContext(ShellContext);
  return (
    <header className="topbar">
      <button type="button" className="hamburger" aria-label={t("nav.open")} onClick={openNav}>☰</button>
      <nav className="crumbs">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <Fragment key={i}>
              {i > 0 && <span className="crumb-sep" aria-hidden="true">/</span>}
              {!last && typeof c === "string" ? <a href="#/">{c}</a> : <span className={last ? "crumb-last" : undefined}>{c}</span>}
            </Fragment>
          );
        })}
      </nav>
      {badge}
      <div className="topbar-actions">{actions}</div>
    </header>
  );
}
