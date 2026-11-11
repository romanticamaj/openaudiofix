import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

const PYTHON = process.env.AFIX_PYTHON ?? (process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");

test("upload → repair → listen → export → rename → narrow → language → delete", async ({ page }, info) => {
  const wav = info.outputPath("glitch.wav");
  execFileSync(PYTHON, ["-c", `from tests.regression.public_cases import write_case; write_case('glitch', r'''${wav}''')`],
    { cwd: "..", env: { ...process.env, PYTHONUTF8: "1" } });

  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.export_dir, "the test server never writes outside its own folder").toBeNull();

  await page.goto("/");
  await page.getByTestId("file-input").setInputFiles(wav);
  const topbar = page.locator(".topbar");
  const start = topbar.getByRole("button", { name: "Start repair" });
  await expect(start).toBeEnabled({ timeout: 90_000 });
  await expect(page.getByRole("switch", { name: /Glitch repair/ })).toBeChecked();
  const declip = page.getByRole("switch", { name: /Declip/ });
  if (await declip.isChecked()) await declip.uncheck();
  await start.click();

  const original = page.getByRole("button", { name: "Original", exact: true });
  await expect(original).toBeVisible({ timeout: 180_000 });
  await page.keyboard.press("1");
  await expect(original).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("3");
  await expect(page.getByRole("button", { name: "Difference", exact: true })).toHaveAttribute("aria-pressed", "true");
  const counter = page.getByTestId("fix-counter");
  await expect(counter).toHaveText(/Fix \d+ \/ [1-9]/);
  const before = await counter.textContent();
  await page.keyboard.press("ArrowRight");
  await expect(counter).not.toHaveText(before!);
  await expect(page.locator(".timeline canvas")).toBeVisible();

  const rows = page.locator("button.fx-row");
  await expect(rows.first()).toBeVisible();
  await rows.nth(1).click();
  await expect(rows.nth(1)).toHaveAttribute("aria-current", "true");

  await topbar.getByRole("button", { name: "Export", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Export" });
  await dialog.getByRole("button", { name: "Export", exact: true }).click();
  const link = dialog.getByRole("link", { name: /_afix\.wav$/ });
  await expect(link).toBeVisible({ timeout: 60_000 });
  const r = await page.request.get((await link.getAttribute("href"))!);
  expect(r.status()).toBe(200);
  expect((await r.body()).subarray(0, 4).toString()).toBe("RIFF");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  await page.locator(".crumbs .rename-name").click();
  await page.getByRole("textbox", { name: "Project name" }).fill("e2e renamed");
  await page.keyboard.press("Enter");
  await expect(page.locator(".crumbs .rename-name")).toHaveText("e2e renamed");
  await expect(page.locator(".sidebar .recent")).toContainText("e2e renamed");

  // spec §10: no horizontal page scroll at 760px and just above it, with the sidebar visible (not the drawer)
  for (const width of [760, 800]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator(".sidebar")).toBeVisible();
    await expect(page.locator(".hamburger")).toBeHidden();
    const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    expect(sizes.scroll, `overflow at ${width}px`).toBeLessThanOrEqual(sizes.inner);
  }
  await page.setViewportSize({ width: 1280, height: 800 });

  await page.goto("/#/settings");
  await page.getByLabel("Language").selectOption("zh-TW");
  await page.goto("/#/");
  await expect(page.locator(".crumbs")).toContainText("專案");

  await page.getByRole("button", { name: "專案操作" }).first().click();
  await page.getByRole("menuitem", { name: "刪除", exact: true }).click();
  await page.getByRole("menuitem", { name: "確定永久刪除？" }).click();
  await expect(page.getByText("e2e renamed")).toHaveCount(0);
});

/** Real playback: a real browser decoding the real preview files. The unit tests use a stand-in for the audio element. */
test("playback continues through every way of switching and seeking", async ({ page }, info) => {
  await page.addInitScript(() => {
    const Real = window.Audio;
    const w = window as unknown as { __audio: HTMLAudioElement[]; Audio: unknown };
    w.__audio = [];
    w.Audio = function (src?: string) { const a = new Real(src); w.__audio.push(a); return a; };
  });
  const wav = info.outputPath("glitch.wav");
  execFileSync(PYTHON, ["-c", `from tests.regression.public_cases import write_case; write_case('glitch', r'''${wav}''')`],
    { cwd: "..", env: { ...process.env, PYTHONUTF8: "1" } });
  await page.goto("/");
  await page.getByTestId("file-input").setInputFiles(wav);
  const start = page.locator(".topbar").getByRole("button", { name: "Start repair" });
  await expect(start).toBeEnabled({ timeout: 90_000 });
  const declip = page.getByRole("switch", { name: /Declip/ });
  if (await declip.isChecked()) await declip.uncheck();
  await start.click();
  const diff = page.getByRole("button", { name: "Difference", exact: true });
  await expect(diff).toBeVisible({ timeout: 180_000 });

  const now = () => page.evaluate(() => (window as unknown as { __audio: HTMLAudioElement[] }).__audio
    .filter((a) => !a.paused && a.error == null).map((a) => ({ file: a.src.split("/").pop()!.split("?")[0], t: a.currentTime })));
  /** Exactly one source plays, it is `file`, and time is really passing. */
  const playing = async (file: RegExp, what: string) => {
    await expect.poll(async () => (await now()).map((x) => x.file).join(), { message: what }).toMatch(file);
    const a = (await now())[0].t;
    await expect.poll(async () => (await now())[0]?.t ?? 0, { message: `${what}: time moves` }).toBeGreaterThan(a + 0.1);
    expect(await now(), what).toHaveLength(1);
  };

  await page.getByRole("button", { name: "Play" }).click();
  await playing(/^s\d+\.webm$/, "play button");
  await diff.click();
  await playing(/^d_chain\.webm$/, "Difference button while playing");
  await page.keyboard.press("1");
  await playing(/^original\.webm$/, "key 1 while playing");
  await page.keyboard.press("3");
  await playing(/^d_chain\.webm$/, "key 3 while playing");
  await page.locator(".timeline").click({ position: { x: 200, y: 40 } });
  await playing(/^d_chain\.webm$/, "click on the waveform while playing");
  // Zoomed in, so the window can move: it must stay where it was put, not be pulled back by playback.
  await page.mouse.move(300, 300);
  const tl = (await page.locator(".timeline").boundingBox())!;
  await page.mouse.move(tl.x + tl.width / 2, tl.y + 40);
  for (let i = 0; i < 6; i++) await page.mouse.wheel(0, -100);
  const mm = (await page.locator(".minimap").boundingBox())!;
  await page.locator(".minimap").click({ position: { x: Math.round(mm.width * 0.7), y: 20 } });
  await playing(/^d_chain\.webm$/, "click on the overview while playing");
  await page.waitForTimeout(500);
  const win = (await page.locator(".mini-win").boundingBox())!;
  // playback may page it forward, never back to where it was
  expect(win.x + win.width / 2, "window not pulled back").toBeGreaterThan(mm.x + mm.width * 0.6);
  await page.getByRole("button", { name: "Show all" }).click();
  await page.keyboard.press("ArrowLeft");
  await playing(/^d_chain\.webm$/, "previous fix while playing");
  await page.getByRole("button", { name: "Pause" }).click();
  await expect.poll(async () => (await now()).length).toBe(0);
  await page.getByRole("button", { name: "Play" }).click();
  await playing(/^d_chain\.webm$/, "play again on Difference");

  // A click puts the playhead exactly under the cursor: on the waveform, zoomed or not, and inside fix boxes.
  await page.getByRole("button", { name: "Pause" }).click();
  const box = (await page.locator(".timeline").boundingBox())!;
  const headX = () => page.locator(".playhead").evaluate((el) => {
    const r = el.getBoundingClientRect();
    return r.left + r.width / 2;
  });
  for (const zoom of [false, true]) {
    for (const f of [0.05, 0.31, 0.5, 0.77, 0.96]) {
      const x = Math.round(box.width * f);
      await page.locator(".timeline").click({ position: { x, y: 40 } });
      await expect.poll(headX, { message: `playhead at ${f} (zoomed: ${zoom})` }).toBeCloseTo(box.x + x, 0);
    }
    await page.mouse.move(box.x + box.width / 2, box.y + 40);
    for (let i = 0; i < 6; i++) await page.mouse.wheel(0, -100);
    await page.waitForTimeout(300);
  }

  // The overview too, with the whole file in view, where its window cannot move.
  await page.getByRole("button", { name: "Show all" }).click();
  const mini = (await page.locator(".minimap").boundingBox())!;
  const time = () => page.evaluate(() => (window as unknown as { __audio: HTMLAudioElement[] }).__audio
    .find((a) => a.src.includes("d_chain.webm"))!.currentTime);
  const dur = await page.evaluate(() => (window as unknown as { __audio: HTMLAudioElement[] }).__audio[0].duration);
  await page.locator(".minimap").click({ position: { x: Math.round(mini.width * 0.2), y: 20 } });
  await expect.poll(time).toBeGreaterThan(dur * 0.19);
  expect(await time()).toBeLessThan(dur * 0.21);
});
