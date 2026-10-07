/**
 * Desktop end-to-end: the real Electron shell starts the real engine, a project is created,
 * geometry imported, the project closed and reopened from the recent list.
 * Set AEROBENCH_E2E_SOLVE=1 to also mesh and solve (needs OpenFOAM; several minutes).
 *
 *   AEROBENCH_PYTHON=../engine/.venv/bin/python xvfb-run -a npx playwright test
 */
import { _electron as electron, expect, test } from "@playwright/test";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

const python = process.env.AEROBENCH_PYTHON || "python3";

test("create, import, close and reopen a project", async () => {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "aerobench-e2e-"));
  const stl = path.join(work, "car.stl");
  execFileSync(python, ["-c", `from aerobench.geometry.fixtures import generic_car; from aerobench.geometry.io import write_stl; write_stl(${JSON.stringify(stl)}, generic_car(rear_wing=True))`]);
  const app = await electron.launch({
    // CI containers run as root, where Chromium's sandbox cannot start
    args: [...(process.getuid && process.getuid() === 0 ? ["--no-sandbox"] : []), path.join(__dirname, "..", "..")],
    env: { ...process.env, AEROBENCH_PYTHON: python, AEROBENCH_CONFIG_DIR: path.join(work, "cfg"), ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  });
  try {
    // the launch window (a data: URL) comes first; the application window follows it
    const mainWindow = async () => {
      const deadline = Date.now() + 120_000;
      while (Date.now() < deadline) {
        // the launch window starts as about:blank before its data: URL loads
        const w = app.windows().find((x) => /^(file|https?):/.test(x.url()));
        if (w) return w;
        await new Promise((r) => setTimeout(r, 250));
      }
      throw new Error("the application window did not open");
    };
    const win = await mainWindow();
    win.on("console", (m) => { if (m.type() === "error") console.log("[renderer]", m.text()); });
    win.on("pageerror", (e) => console.log("[pageerror]", e.message));
    await expect(win.getByText("Recent projects")).toBeVisible({ timeout: 90_000 });
    await expect(win.getByText("This computer")).toBeVisible();

    await win.getByLabel("Name", { exact: true }).fill("E2E car");
    await win.getByLabel("Keep it in").fill(work);
    await win.getByRole("button", { name: "Create project" }).click();
    await expect(win.getByText("E2E car").first()).toBeVisible();

    await win.getByPlaceholder("Or paste a file path and press Enter").fill(stl);
    await win.getByPlaceholder("Or paste a file path and press Enter").press("Enter");
    await expect(win.getByText("No problems found in the surface.")).toBeVisible({ timeout: 60_000 });
    await expect(win.getByText("wheel_FL").first()).toBeVisible();

    if (process.env.AEROBENCH_E2E_SOLVE === "1") {
      // a quick but complete pipeline: fast mesh preset, short solve
      test.setTimeout(45 * 60_000);
      await win.locator('nav >> text="Mesh"').click();
      await win.getByText("Fast", { exact: true }).first().click();
      await expect(win.getByText("Expected cells")).toBeVisible();
      await win.locator('nav >> text="Physics"').click();
      const iterations = win.getByRole("textbox", { name: "Iterations", exact: true });
      await iterations.fill("300");
      await iterations.press("Enter");
      await win.locator('nav >> text="Run"').click();
      await win.getByRole("button", { name: "Run simulation" }).click();
      await expect(win.getByText("COMPLETED").first()).toBeVisible({ timeout: 40 * 60_000 });
    }

    await win.locator('nav >> text="Project"').click();
    await win.getByRole("button", { name: "Close project" }).click();
    await expect(win.getByText("Recent projects")).toBeVisible();
    await win.getByText(/E2E.car/).first().click();
    await expect(win.getByText("synthetic_car.stl").or(win.getByText("car.stl")).first()).toBeVisible({ timeout: 30_000 });
  } catch (e) {
    const w = app.windows().find((x) => /^(file|https?):/.test(x.url())) ?? app.windows()[0];
    if (w) await w.screenshot({ path: "test-results/e2e-failure.png" }).catch(() => undefined);
    throw e;
  } finally {
    await app.close();
  }
});
