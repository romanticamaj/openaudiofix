import { act, render, screen, within, cleanup } from "@testing-library/react";
import { ModulesPage } from "./ModulesPage";
import { MODULES_CHANGED } from "../api";

class FakeES {
  static all: FakeES[] = [];
  constructor() { FakeES.all.push(this); }
  onmessage: ((m: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close() {}
}

const mods = {
  modules: [
    { id: "denoise", name: "Denoise", device: "gpu", state: "not_installed", step: null, detail: {} },
    { id: "glitch", name: "Glitch", device: "cpu", state: "installed", step: null, detail: {} },
    { id: "declip", name: "Declip", device: "cpu", state: "installed", step: null, detail: {} },
  ],
};

beforeEach(() => {
  FakeES.all = [];
  (globalThis as any).EventSource = FakeES;
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(mods))) as any;
});
afterEach(cleanup);

test("renders three module cards in glitch, declip, denoise order", async () => {
  const { container } = render(<ModulesPage />);
  await screen.findByText("Glitch repair");
  const titles = Array.from(container.querySelectorAll(".module-card h3")).map((h) => h.textContent);
  expect(titles).toEqual(["Glitch repair", "Declip", "Background repair"]);
});

test("CPU cards say Built in; the GPU card offers Check GPU", async () => {
  const { container } = render(<ModulesPage />);
  await screen.findByText("Glitch repair");
  const cards = Array.from(container.querySelectorAll<HTMLElement>(".module-card"));
  expect(within(cards[0]).getByText("Built in")).toBeTruthy();
  expect(within(cards[1]).getByText("Built in")).toBeTruthy();
  expect(within(cards[2]).queryByText("Built in")).toBeNull();
  act(() => FakeES.all[0].onmessage?.({ data: JSON.stringify({ id: "denoise", state: "not_installed", step: null, detail: {}, updated: 0 }) }));
  expect(await within(cards[2]).findByRole("button", { name: "Check GPU" })).toBeTruthy();
  expect((globalThis.fetch as any).mock.calls.some((c: any[]) => String(c[0]).endsWith("/install"))).toBe(false);
});

test("a GPU state from the panel is broadcast to the sidebar", async () => {
  const seen: any[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  addEventListener(MODULES_CHANGED, on);
  render(<ModulesPage />);
  await screen.findByText("Glitch repair");
  act(() => FakeES.all[0].onmessage?.({ data: JSON.stringify({ id: "denoise", state: "installing", step: "pull", detail: {}, updated: 5 }) }));
  removeEventListener(MODULES_CHANGED, on);
  expect(seen.at(-1)).toMatchObject({ id: "denoise", state: "installing" });
});

test("shows an inline error when the module list fails to load", async () => {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ code: "internal_error", message: "boom" }), { status: 500 })) as any;
  render(<ModulesPage />);
  expect((await screen.findByRole("alert")).textContent).toContain("Something went wrong.");
});

test("the models section lists each model; the speech model can be downloaded, GPU ones are managed by the module", async () => {
  const models = {
    dir: "D:\models", can_open_folder: false,
    models: [
      { id: "silero", name: "Silero VAD v5", used_by: ["speech"], license: "MIT", state: "missing", size_bytes: 0,
        expected_bytes: 2.3e6, path: "D:\models\s.onnx", managed_by: null, device: "cpu" },
      { id: "mossformer2", name: "MossFormer2 SE 48 kHz", used_by: ["denoise"], license: "Apache-2.0", state: "ready",
        size_bytes: 2e8, expected_bytes: 2e8, path: "D:\models\m", managed_by: "denoise", device: "gpu" },
    ],
  };
  const calls: string[] = [];
  globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (url === "/api/models") return new Response(JSON.stringify(models));
    if (url === "/api/models/silero/download") return new Response(JSON.stringify({ ...models.models[0], state: "ready", size_bytes: 2.3e6 }));
    return new Response(JSON.stringify(mods));
  }) as any;
  render(<ModulesPage />);
  expect(await screen.findByText("Silero VAD v5")).toBeTruthy();
  expect(screen.getByText("D:\models")).toBeTruthy();
  expect(screen.getByText(/Installed and removed with/)).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(1); // the GPU model has no button of its own
  await act(async () => { screen.getByRole("button", { name: "Download" }).click(); });
  expect(calls).toContain("POST /api/models/silero/download");
  expect(await screen.findByRole("button", { name: "Delete" })).toBeTruthy();
});
