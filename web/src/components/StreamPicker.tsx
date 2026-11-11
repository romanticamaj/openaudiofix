import { useState } from "react";
import type { Project } from "../api";
import { useT } from "../i18n";

/** Pick the audio track of a multi-stream file. The page runs the choice (toast on error, refresh). */
export function StreamPicker({ project, onChoose, busy = false }: { project: Project; onChoose: (index: number) => void; busy?: boolean }) {
  const { t } = useT();
  const streams = project.streams ?? [];
  const [pick, setPick] = useState(streams[0]?.index ?? 0);
  return (
    <section className="card state-card">
      <h3>{t("stream.title")}</h3>
      {streams.map((s) => (
        <label key={s.index} className="radio-row">
          <input type="radio" name="stream" checked={pick === s.index} onChange={() => setPick(s.index)} />
          {t("stream.option", { n: s.index + 1, codec: s.codec, channels: s.channels, sr: s.sr })}
        </label>
      ))}
      <button type="button" className="primary" disabled={busy} onClick={() => onChoose(pick)}>
        {t("stream.use")}
      </button>
    </section>
  );
}
