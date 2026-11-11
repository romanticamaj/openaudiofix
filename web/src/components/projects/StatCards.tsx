import { useT } from "../../i18n";

export type Stat = { key: string; label: string; value: string | number; text?: boolean };

export function StatCards({ stats }: { stats: Stat[] }) {
  const { t } = useT();
  return (
    <section className="stats" aria-label={t("nav.projects")}>
      {stats.map((s) => (
        <div className="stat" key={s.key}>
          <div className="stat-label">{s.label}</div>
          <div className={"stat-value" + (s.text ? " text" : " mono")}>{s.value}</div>
        </div>
      ))}
    </section>
  );
}
