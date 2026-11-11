import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import en from "./en.json";
import zh from "./zh-TW.json";
import { translate } from "./index";

test("both languages have exactly the same keys", () => {
  expect(Object.keys(zh).sort()).toEqual(Object.keys(en).sort());
});

test("every server error code has a translation", () => {
  const py = readFileSync(resolve(__dirname, "../../../server/errors.py"), "utf-8");
  const block = py.slice(py.indexOf("CODES = ("), py.indexOf(")", py.indexOf("CODES = (")));
  const codes = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  expect(codes.length).toBeGreaterThan(20);
  for (const c of codes) expect(Object.keys(en)).toContain(`error.${c}`);
});

test("every GPU module step the server records has a translation", () => {
  const py = readFileSync(resolve(__dirname, "../../../server/gpu_manager.py"), "utf-8");
  const block = py.slice(py.indexOf("STEPS = ("), py.indexOf(")", py.indexOf("STEPS = (")));
  const steps = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  expect(steps).toEqual(expect.arrayContaining(["precheck", "confirm", "download", "start", "verify", "remove", "done"]));
  for (const s of steps) {
    expect(Object.keys(en)).toContain(`gpu.step.${s}`);
    expect(Object.keys(zh)).toContain(`gpu.step.${s}`);
  }
});

test("placeholders are filled and unknown keys fall back", () => {
  expect(translate("en", "results.fixCounter", { k: 3, n: 10 })).toBe("Fix 3 / 10");
  expect(translate("zh-TW", "results.fixCounter", { k: 3, n: 10 })).toBe("修正 3 / 10");
  expect(translate("zh-TW", "no.such.key")).toBe("no.such.key");
});
