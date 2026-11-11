import type { ReactNode } from "react";

/** A titled group of rows. */
export function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="set-section">
      <h3>{title}</h3>
      {description ? <p className="muted set-desc">{description}</p> : null}
      <div className="set-rows">{children}</div>
    </section>
  );
}

/** One setting: label and description on the left, the control on the right, optional detail below. */
export function SettingRow({ title, description, action, children }: {
  title: string; description?: ReactNode; action?: ReactNode; children?: ReactNode;
}) {
  return (
    <div className="set-row">
      <div className="set-row-top">
        <div className="set-row-text">
          <div className="set-row-title">{title}</div>
          {description ? <div className="set-row-desc muted">{description}</div> : null}
        </div>
        {action ? <div className="set-row-action">{action}</div> : null}
      </div>
      {children ? <div className="set-row-more">{children}</div> : null}
    </div>
  );
}
