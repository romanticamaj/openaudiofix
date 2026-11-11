import { useT } from "../../i18n";
import { stateGroup, TONE } from "../../status";

export function Badge({ state }: { state: string }) {
  const { t } = useT();
  const group = stateGroup(state);
  return (
    <span className={"badge " + TONE[group]}>
      {group === "busy" && <i className="pulse-dot" aria-hidden="true" />}
      {t("state." + state)}
    </span>
  );
}
