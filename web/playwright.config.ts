import { defineConfig } from "@playwright/test";

const PORT = Number(process.env.AFIX_E2E_PORT ?? 8813);
const STAMP = Date.now();
const PYTHON = process.env.AFIX_PYTHON ?? (process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: `"${PYTHON}" -m server.launcher`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      AFIX_PORT: String(PORT), AFIX_DATA: `.e2e-data/${STAMP}`, AFIX_WEB_DIST: "web/dist", PYTHONUTF8: "1",
      // nothing outside the test's own folder: no saved locations, no exports into the real Documents folder
      AFIX_CONFIG_DIR: `.e2e-data/${STAMP}-config`, AFIX_DEFAULT_EXPORT: "",
    },
  },
});
