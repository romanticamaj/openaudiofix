import type { ApiErr } from "../api";
import { ApiError } from "../api";
import { useT } from "../i18n";

export function ErrorBox({ error }: { error: ApiErr | ApiError | null }) {
  const { t } = useT();
  if (!error) return null;
  const key = `error.${error.code}`;
  const translated = t(key);
  // t() returns the key itself when no translation exists; then show the server's message.
  const text = translated === key ? error.message : translated;
  return (
    <div className="error" role="alert">
      <strong>{text}</strong>
      {error.hint ? <p>{error.hint}</p> : null}
    </div>
  );
}
