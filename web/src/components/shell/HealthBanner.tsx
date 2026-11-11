import { useEffect, useState } from "react";
import { api, type Health } from "../../api";
import { useT } from "../../i18n";

export function HealthBanner() {
  const { t } = useT();
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ ok: false, checks: [{ id: "server", ok: false, message: "" }] }));
  }, []);
  if (!health || health.ok) return null;
  return (
    <div className="banner" role="alert">
      {t("health.banner", { checks: health.checks.filter((c) => !c.ok).map((c) => t(`health.${c.id}`)).join(", ") })}
    </div>
  );
}
