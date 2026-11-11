import { fireEvent, render, screen } from "@testing-library/react";
import type { Project } from "../../api";
import { OutputChoice, peaksOver } from "./OutputChoice";

const proj = (o: Partial<Project>, gain = 0, exact = true): Project => ({
  id: "p", name: "a.wav", created: 0, state: "done", sr: 48000, channels: 2, duration_s: 10, codec: null, lossy: false,
  has_video: false, streams: null, modules: ["glitch"], scan: null, error: null, size_bytes: 0, subtype: "PCM_24",
  result: { export_gain_db: gain, byte_exact_possible: exact } as any, ...o,
});
test("peaks over full scale are the one real choice, described by what each keeps", () => {
  const onFloat = vi.fn();
  const p = proj({ modules: ["declip"] }, -2.5, false);
  expect(peaksOver(p)).toBe(true);
  expect(peaksOver(proj({}, 0))).toBe(false);
  expect(peaksOver(proj({ state: "processing" }, -2.5))).toBe(false);
  render(<OutputChoice project={p} float onFloat={onFloat} />);
  expect((screen.getByRole("radio", { name: /Keep the level and waveform/ }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: /Keep 24-bit/ }));
  expect(onFloat).toHaveBeenCalledWith(false);
  expect(screen.getByText(/lowered by 2.5 dB/)).toBeTruthy();
});
