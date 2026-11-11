import { useState } from "react";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import * as apiMod from "../api";
import { I18nContext, translate, type Lang } from "../i18n";
import { SettingsPage } from "./SettingsPage";

function Wrap() {
  const [lang, setLang] = useState<Lang>("en");
  return (
    <I18nContext.Provider value={{ lang, setLang, t: (k, v) => translate(lang, k, v) }}>
      <SettingsPage />
    </I18nContext.Provider>
  );
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.spyOn(apiMod.api, "projects").mockResolvedValue({ projects: [], total_bytes: 3e9, free_bytes: 5e10 });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("selecting zh-TW switches the page heading to 設定", async () => {
  render(<Wrap />);
  expect(screen.getAllByText("Settings").length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText("Language"), { target: { value: "zh-TW" } });
  expect((await screen.findAllByText("設定")).length).toBeGreaterThan(0);
});

test("clicking Light applies and saves the theme", () => {
  render(<Wrap />);
  fireEvent.click(screen.getByRole("button", { name: "Light" }));
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(localStorage.getItem("afix.theme")).toBe("light");
  expect(screen.getByRole("button", { name: "Light" }).getAttribute("aria-pressed")).toBe("true");
});

test("storage tab shows the folders and their sizes", async () => {
  vi.spyOn(apiMod.api, "settings").mockResolvedValue({
    export_dir: null, data_dir: "D:/afix/data", models_dir: "D:/afix/models", data_bytes: 3e9, models_bytes: 2.3e6,
    free_bytes: 5e10, sources: { data: "settings", models: "env" }, can_browse: true, can_open_folder: true,
    in_container: false, can_restart: true, saved: {}, move: { state: "idle" },
    defaults: { data: "C:/app/data", models: "C:/app/models", export: "C:/docs/Exports" }, in_temp: { data: true, models: false },
  } as any);
  render(<I18nContext.Provider value={{ lang: "en", setLang: () => {}, t: (k, v) => translate("en", k, v) }}><SettingsPage tab="storage" /></I18nContext.Provider>);
  expect(await screen.findByText("D:/afix/data")).toBeTruthy();
  expect(screen.getByText(/3\.0 GB used/)).toBeTruthy();
  expect(screen.getByText(/50\.0 GB free/)).toBeTruthy();
  expect(screen.getByText(/set by AFIX_MODELS/)).toBeTruthy();
  expect(screen.getByText(/inside the system temp folder/)).toBeTruthy();
  expect(screen.getByText("C:/app/data")).toBeTruthy(); // the usual place, offered when the folder is elsewhere
  expect(screen.getByRole("button", { name: "Use default" })).toBeTruthy();
});

test("about shows version and licence", () => {
  render(<I18nContext.Provider value={{ lang: "en", setLang: () => {}, t: (k, v) => translate("en", k, v) }}><SettingsPage tab="about" /></I18nContext.Provider>);
  expect(screen.getByText("OpenAudioFix")).toBeTruthy();
  expect(screen.getByText(/MIT License/)).toBeTruthy();
});
