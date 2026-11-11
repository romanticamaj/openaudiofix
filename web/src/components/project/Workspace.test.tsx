import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import type { Project } from "../../api";
import type { View } from "../../timelineMath";
import { FixList } from "./FixList";
import { useResultsTransport } from "./useResultsTransport";
import { Workspace } from "./Workspace";

class FakeAudio {
  currentTime = 0; paused = true; preload = ""; src = "";
  play() { this.paused = false; return Promise.resolve(); }
  pause() { this.paused = true; }
  addEventListener() {}
}

const FIXES = [
  { module: "glitch", kind: "seam", channel: -1, start: 48000 * 5, end: 48000 * 5 + 100, seconds: 5 },
  { module: "declip", kind: "clip+", channel: 0, start: 48000 * 9, end: 48000 * 9 + 6, seconds: 9 },
];

const project = {
  id: "p1", name: "a.wav", state: "done", sr: 48000, channels: 1, duration_s: 20, modules: ["glitch", "declip"],
  result: {
    preview_gain_db: -3.2, export_gain_db: -3.2, byte_exact_possible: false, counts: { glitch: 1, declip: 1 }, notes: {},
    sources: [{ id: "original" }, { id: "s0", module: "glitch" }, { id: "s1", module: "declip" }],
    diffs: [{ id: "d_chain", gain_db: 20 }, { id: "d0", module: "glitch", gain_db: 25 }, { id: "d1", module: "declip", gain_db: 22 }],
  },
} as unknown as Project;
const cleanedProject = { ...project, result: { ...project.result!, cleaned: true } } as Project;

let fixes: unknown[] = FIXES;
let made: FakeAudio[] = [];

beforeEach(() => {
  made = [];
  fixes = FIXES;
  (globalThis as any).Audio = class extends FakeAudio { constructor() { super(); made.push(this); } };
  globalThis.fetch = vi.fn(async (url: string) => {
    if (url.endsWith("/fixes")) return new Response(JSON.stringify({ fixes }));
    return new Response(JSON.stringify({ peaks: [] }));
  }) as any;
});

const audio = (end: string) => made.filter((a) => a.src.endsWith(end)).at(-1)!;

function Harness({ project: p }: { project: Project }) {
  const [selected, setSelected] = useState<number | null>(null);
  const rt = useResultsTransport(p, setSelected);
  const [view, setView] = useState<View>({ t0: 0, t1: p.duration_s ?? 0 });
  return (
    <>
      <button type="button">Top bar action</button>
      <Workspace project={p} rt={rt} view={view} onView={setView} />
      <FixList fixes={rt.fixes} selected={selected != null ? rt.points[selected] ?? null : null} sr={p.sr ?? 48000}
               duration={p.duration_s ?? 0} onPick={(s, row) => { rt.tp.seekToFix(s); setSelected(rt.points.indexOf(row)); }} />
    </>
  );
}

const seg = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

test("segmented sources, keys 1/2/3, the stage select and the level notes", async () => {
  render(<Harness project={project} />);
  await screen.findByText("Fix 0 / 2");
  expect(seg("Repaired").getAttribute("aria-pressed")).toBe("true");
  expect(seg("Repaired").querySelector(".kbd")?.textContent).toBe("2");
  fireEvent.keyDown(document, { key: "1" });
  expect(seg("Original").getAttribute("aria-pressed")).toBe("true");
  expect(seg("Repaired").getAttribute("aria-pressed")).toBe("false");
  fireEvent.keyDown(document, { key: "3" });
  expect(seg("Difference").getAttribute("aria-pressed")).toBe("true");
  fireEvent.keyDown(document, { key: "2" });
  expect(seg("Repaired").getAttribute("aria-pressed")).toBe("true");
  // The intermediate stage stays reachable through the stage select.
  const stage = screen.getByRole("combobox", { name: "Stage" }) as HTMLSelectElement;
  expect(within(stage).getByRole("option", { name: "After Glitch repair" })).toBeTruthy();
  fireEvent.change(stage, { target: { value: "s0" } });
  expect(stage.value).toBe("s0");
  expect(seg("Repaired").getAttribute("aria-pressed")).toBe("false");
  expect(audio("/s0.webm").currentTime).toBe(0);
  expect(screen.getByText(/plays 3.2 dB quieter/)).toBeTruthy();
  fireEvent.keyDown(document, { key: "3" });
  expect(screen.getByText(/raised 20.0 dB/)).toBeTruthy();
});

test("keys 3 and 2 switch the playing audio element and keep the position; space toggles from workspace buttons", async () => {
  render(<Harness project={project} />);
  await screen.findByText("Fix 0 / 2");
  const fin = audio("/s1.webm"), diff = audio("/d_chain.webm");
  fireEvent.keyDown(document, { key: " " });
  expect(fin.paused).toBe(false);
  expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
  fin.currentTime = 7;
  fireEvent.keyDown(document, { key: "3" });
  expect(fin.paused).toBe(true);
  expect(diff.paused).toBe(false);
  expect(diff.currentTime).toBe(7);
  fireEvent.keyDown(document, { key: "2" });
  expect(diff.paused).toBe(true);
  expect(fin.paused).toBe(false);
  expect(fireEvent.keyDown(seg("Original"), { key: " " })).toBe(false); // a workspace button: Space plays/pauses
  expect(fireEvent.keyUp(seg("Original"), { key: " " })).toBe(false); // ...and its own keyup activation is suppressed
  expect(fin.paused).toBe(true);
  expect(seg("Repaired").getAttribute("aria-pressed")).toBe("true");
  fireEvent.keyDown(document.body, { key: " " });
  expect(fin.paused).toBe(false);
  fireEvent.keyDown(screen.getByRole("button", { name: "Pause" }), { key: " " }); // the transport bar's own button
  expect(fin.paused).toBe(true);
  expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
});

test("Space after clicking a fix row toggles playback", async () => {
  render(<Harness project={project} />);
  await screen.findByText("Fix 0 / 2");
  const row = screen.getAllByRole("button").find((b) => b.classList.contains("fx-row"))!;
  row.focus();
  fireEvent.click(row);
  expect(row.getAttribute("aria-current")).toBe("true");
  const fin = audio("/s1.webm");
  expect(fireEvent.keyDown(row, { key: " " })).toBe(false);
  expect(fin.paused).toBe(false);
  expect(fireEvent.keyDown(row, { key: " " })).toBe(false);
  expect(fin.paused).toBe(true);
});

test("Space on a button outside the workspace keeps its native behaviour", async () => {
  render(<Harness project={project} />);
  await screen.findByText("Fix 0 / 2");
  const outside = screen.getByRole("button", { name: "Top bar action" });
  outside.focus();
  expect(fireEvent.keyDown(outside, { key: " " })).toBe(true);
  expect(fireEvent.keyUp(outside, { key: " " })).toBe(true);
  expect(audio("/s1.webm").paused).toBe(true);
  expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
});

test("arrow keys and the Previous/Next buttons step through fixes and move the selection", async () => {
  render(<Harness project={project} />);
  await screen.findByText("Fix 0 / 2");
  const counter = screen.getByTestId("fix-counter");
  const selectedRow = () => document.querySelector('.fx-row[aria-current="true"]')?.textContent ?? "";
  fireEvent.keyDown(document, { key: "ArrowRight" });
  expect(counter.textContent).toBe("Fix 1 / 2");
  expect(selectedRow()).toMatch(/0:05\.0/);
  fireEvent.keyDown(document, { key: "ArrowRight" });
  expect(counter.textContent).toBe("Fix 2 / 2");
  expect(selectedRow()).toMatch(/0:09\.0/);
  fireEvent.keyDown(document, { key: "ArrowLeft" });
  expect(counter.textContent).toBe("Fix 1 / 2");
  expect(selectedRow()).toMatch(/0:05\.0/);
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(counter.textContent).toBe("Fix 2 / 2");
  expect(selectedRow()).toMatch(/0:09\.0/);
  fireEvent.click(screen.getByRole("button", { name: "Previous" }));
  expect(counter.textContent).toBe("Fix 1 / 2");
  expect(screen.getByText(/0:04\.0 \/ 0:20\.0/)).toBeTruthy();
});

test("a whole-file denoise fix is not counted and shows as one row; clicking a fix jumps 1 s before", async () => {
  fixes = [
    { module: "denoise", kind: "denoise", channel: -1, start: 0, end: 48000 * 20, seconds: 0 },
    { module: "glitch", kind: "seam", channel: -1, start: 48000 * 5, end: 48000 * 5 + 100, seconds: 5 },
  ];
  const withDenoise = {
    ...project, modules: ["glitch", "denoise"],
    result: { ...project.result!, sources: [{ id: "original" }, { id: "s0", module: "glitch" }, { id: "s1", module: "denoise" }] },
  } as Project;
  render(<Harness project={withDenoise} />);
  await screen.findByText("Fix 0 / 1");
  expect(screen.getAllByText(/Whole file/).length).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: /0:05\.0/ }));
  expect(screen.getByText(/0:04\.0 \/ 0:20\.0/)).toBeTruthy();
  expect(document.querySelector('.fx-row[aria-current="true"]')?.textContent).toMatch(/0:05\.0/);
});

test("the diff select shows only with more than one difference", async () => {
  const { unmount } = render(<Harness project={project} />);
  await screen.findByText("Fix 0 / 2");
  expect(screen.getByRole("combobox", { name: "Difference" })).toBeTruthy();
  unmount();
  const one = { ...project, result: { ...project.result!, diffs: [{ id: "d_chain", gain_db: 20 }] } } as Project;
  render(<Harness project={one} />);
  await screen.findByText("Fix 0 / 2");
  expect(screen.queryByRole("combobox", { name: "Difference" })).toBeNull();
});

test("after cleanup only original, final and the chain difference remain", async () => {
  render(<Harness project={cleanedProject} />);
  await screen.findByText("Fix 0 / 2");
  expect(screen.queryByRole("combobox", { name: "Stage" })).toBeNull();
  expect(seg("Repaired")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Difference" })).toBeNull();
  expect(screen.getByText(/Intermediate files were removed/)).toBeTruthy();
});

test("cleanup flipping the project keeps the page live: arrow keys still update the counter", async () => {
  const view = render(<Harness project={project} />);
  await screen.findByText("Fix 0 / 2");
  view.rerender(<Harness project={cleanedProject} />);
  await screen.findByText(/Intermediate files were removed/);
  await screen.findByText("Fix 0 / 2");
  fireEvent.keyDown(document, { key: "ArrowRight" });
  expect(screen.getByTestId("fix-counter").textContent).toBe("Fix 1 / 2");
});

test("keys are ignored inside text fields and contenteditable", async () => {
  render(<><Harness project={project} /><input aria-label="t" /><div contentEditable data-testid="ce" /></>);
  await screen.findByText("Fix 0 / 2");
  fireEvent.keyDown(screen.getByLabelText("t"), { key: "1" });
  fireEvent.keyDown(screen.getByTestId("ce"), { key: "1" });
  fireEvent.keyDown(screen.getByTestId("ce"), { key: " " });
  expect(seg("Repaired").getAttribute("aria-pressed")).toBe("true");
  expect(audio("/s1.webm").paused).toBe(true);
});

test("Zoom to fit resets the view", async () => {
  const onView = vi.fn();
  function Z() {
    const rt = useResultsTransport(project);
    return <Workspace project={project} rt={rt} view={{ t0: 3, t1: 5 }} onView={onView} />;
  }
  render(<Z />);
  await screen.findByText("Fix 0 / 2");
  fireEvent.click(screen.getByRole("button", { name: "Show all" }));
  expect(onView).toHaveBeenCalledWith({ t0: 0, t1: 20 });
});
