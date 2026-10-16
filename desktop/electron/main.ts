/**
 * Electron main process.
 *
 * Responsibilities: start the Python engine service (localhost, per-launch token), open the
 * window, native dialogs, opening files/ParaView externally and printing reports to PDF.
 * Solver jobs are owned by detached runner processes, so closing the window (or a crash
 * here) never kills a running simulation.
 */
import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from "electron";
import { ChildProcess, spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as readline from "readline";
import { splashHtml } from "./splash";

interface EngineInfo {
  url: string;
  token: string;
  pid: number;
}

let engine: ChildProcess | null = null;
let engineInfo: EngineInfo | null = null;
let engineLog: string[] = [];
let win: BrowserWindow | null = null;
let splash: BrowserWindow | null = null;

/** Launch window: appears immediately, reports start-up progress, closes when the app shows. */
function showSplash(): void {
  splash = new BrowserWindow({
    width: 560,
    height: 340,
    frame: false,
    resizable: false,
    movable: true,
    center: true,
    show: false,
    skipTaskbar: false,
    title: "AeroBench",
    backgroundColor: "#15181d",
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, javascript: true },
  });
  splash.once("ready-to-show", () => splash?.show());
  splash.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(splashHtml(app.getVersion())));
  splash.on("closed", () => (splash = null));
}

function splashStatus(text: string, isError = false): void {
  if (!splash || splash.isDestroyed()) return;
  splash.webContents.executeJavaScript(`window.setStatus && window.setStatus(${JSON.stringify(text)}, ${isError})`).catch(() => undefined);
}

function closeSplash(): void {
  if (splash && !splash.isDestroyed()) splash.close();
  splash = null;
}

const isDev = !!process.env.AEROBENCH_DEV_URL;

function resourcesPath(...p: string[]): string {
  return app.isPackaged ? path.join(process.resourcesPath, ...p) : path.join(__dirname, "..", "..", ...p);
}

/** Resolve the Python interpreter that hosts the engine. */
function pythonCommand(): { cmd: string; args: string[] } {
  if (process.env.AEROBENCH_PYTHON) return { cmd: process.env.AEROBENCH_PYTHON, args: [] };
  const bundled =
    process.platform === "win32" ? resourcesPath("python", "python.exe") : resourcesPath("python", "bin", "python3");
  if (fs.existsSync(bundled)) return { cmd: bundled, args: [] };
  const venv =
    process.platform === "win32"
      ? resourcesPath("engine", ".venv", "Scripts", "python.exe")
      : resourcesPath("engine", ".venv", "bin", "python");
  if (fs.existsSync(venv)) return { cmd: venv, args: [] };
  return process.platform === "win32" ? { cmd: "py", args: ["-3"] } : { cmd: "python3", args: [] };
}

function startEngine(): Promise<EngineInfo> {
  return new Promise((resolve, reject) => {
    const { cmd, args } = pythonCommand();
    // The engine serves the renderer from a real folder: in a packaged app the build output is
    // unpacked from the asar archive (see "asarUnpack" in the electron-builder config).
    const staticDir = isDev
      ? undefined
      : app.isPackaged
        ? path.join(process.resourcesPath, "app.asar.unpacked", "dist")
        : path.join(__dirname, "..", "dist");
    const fullArgs = [...args, "-m", "aerobench.api.server"];
    if (staticDir) fullArgs.push("--static", staticDir);
    if (isDev) fullArgs.push("--dev");
    const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: "1" };
    // OpenFOAM, ParaView and the MS-MPI installer ship in the resources folder (see packaging)
    if (app.isPackaged) env.AEROBENCH_BUNDLE_DIR = process.resourcesPath;
    const enginePkg = resourcesPath("engine", "src");
    if (fs.existsSync(enginePkg)) env.PYTHONPATH = enginePkg + (env.PYTHONPATH ? path.delimiter + env.PYTHONPATH : "");
    engine = spawn(cmd, fullArgs, { env, windowsHide: true });
    const timer = setTimeout(() => reject(new Error("The engine did not start within 60 s.\n" + engineLog.join("\n"))), 60000);
    readline.createInterface({ input: engine.stdout! }).on("line", (line) => {
      engineLog.push(line);
      engineLog = engineLog.slice(-400);
      if (line.startsWith("AEROBENCH_READY ")) {
        const info = JSON.parse(line.slice("AEROBENCH_READY ".length));
        clearTimeout(timer);
        engineInfo = { url: `http://127.0.0.1:${info.port}`, token: info.token, pid: info.pid };
        resolve(engineInfo);
      }
    });
    readline.createInterface({ input: engine.stderr! }).on("line", (line) => {
      engineLog.push(line);
      engineLog = engineLog.slice(-400);
    });
    engine.on("exit", (code) => {
      clearTimeout(timer);
      if (!engineInfo) reject(new Error(`The engine exited (code ${code}).\n` + engineLog.join("\n")));
      else if (win && !win.isDestroyed()) win.webContents.send("engine:exited", { code, log: engineLog.slice(-60) });
      engine = null;
    });
    engine.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`Could not start the engine with '${cmd}': ${err.message}`));
    });
  });
}

async function createWindow(): Promise<void> {
  win = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1100,
    minHeight: 700,
    title: "AeroBench",
    backgroundColor: "#0f1115",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.once("ready-to-show", () => {
    win?.show();
    win?.focus();
    closeSplash();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://127.0.0.1") || url.startsWith("https://")) shell.openExternal(url);
    return { action: "deny" };
  });
  const target = isDev ? process.env.AEROBENCH_DEV_URL! : engineInfo!.url + "/";
  // The engine listens before it announces its port, so the first load normally succeeds;
  // retry briefly anyway rather than ever leaving the user on a browser error page.
  for (let attempt = 0; ; attempt++) {
    try {
      await win.loadURL(target);
      return;
    } catch (err) {
      if (!win || win.isDestroyed()) return;
      if (attempt >= 40) throw err;
      await new Promise((r) => setTimeout(r, 250));
    }
  }
}

function registerIpc(): void {
  ipcMain.handle("engine:info", () => engineInfo);
  ipcMain.handle("engine:log", () => engineLog.slice(-200));
  ipcMain.handle("dialog:openFile", async (_e, opts: { title?: string; filters?: Electron.FileFilter[] }) => {
    const r = await dialog.showOpenDialog(win!, { title: opts?.title, properties: ["openFile"], filters: opts?.filters });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle("dialog:openFiles", async (_e, opts: { title?: string; filters?: Electron.FileFilter[] }) => {
    const r = await dialog.showOpenDialog(win!, { title: opts?.title, properties: ["openFile", "multiSelections"], filters: opts?.filters });
    return r.canceled ? [] : r.filePaths;
  });
  ipcMain.handle("dialog:openDirectory", async (_e, opts: { title?: string; create?: boolean }) => {
    const props: ("openDirectory" | "createDirectory" | "promptToCreate")[] = ["openDirectory"];
    if (opts?.create) props.push("createDirectory", "promptToCreate");
    const r = await dialog.showOpenDialog(win!, { title: opts?.title, properties: props });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle("dialog:saveFile", async (_e, opts: { title?: string; defaultPath?: string; filters?: Electron.FileFilter[] }) => {
    const r = await dialog.showSaveDialog(win!, { title: opts?.title, defaultPath: opts?.defaultPath, filters: opts?.filters });
    return r.canceled ? null : r.filePath;
  });
  ipcMain.handle("shell:openPath", (_e, p: string) => shell.openPath(p));
  ipcMain.handle("shell:showItem", (_e, p: string) => shell.showItemInFolder(p));
  ipcMain.handle("paraview:open", (_e, exe: string, file: string) => {
    if (!exe || !fs.existsSync(exe)) return { ok: false, error: "ParaView executable not found" };
    // a generated state script (opens decomposed results at the latest time) or a data file
    const arg = file.toLowerCase().endsWith(".py") ? `--script=${file}` : `--data=${file}`;
    const child = spawn(exe, [arg], { detached: true, stdio: "ignore", windowsHide: false });
    child.unref();
    return { ok: true };
  });
  ipcMain.handle("pdf:fromUrl", async (_e, url: string, outPath: string) => {
    const w = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true } });
    try {
      await w.loadURL(url);
      const pdf = await w.webContents.printToPDF({ printBackground: true, pageSize: "A4" });
      fs.writeFileSync(outPath, pdf);
      return { ok: true, path: outPath };
    } finally {
      w.destroy();
    }
  });
  ipcMain.handle("app:versions", () => ({ app: app.getVersion(), electron: process.versions.electron, chrome: process.versions.chrome }));
  // Install the bundled Microsoft MPI (normally done by the setup; this is the repair path).
  // The path is fixed here — never taken from the renderer — and Windows asks for elevation.
  ipcMain.handle("msmpi:install", async () => {
    if (process.platform !== "win32") return { ok: false, error: "Microsoft MPI is only needed on Windows." };
    const setup = resourcesPath("redist", "msmpisetup.exe");
    if (!fs.existsSync(setup)) return { ok: false, error: "The bundled Microsoft MPI installer is missing; run the AeroBench setup again." };
    const q = (s: string) => "'" + s.replace(/'/g, "''") + "'";
    const ps = `$p = Start-Process -FilePath ${q(setup)} -ArgumentList '-unattend' -WorkingDirectory ${q(path.dirname(setup))} -Verb RunAs -Wait -PassThru; exit $p.ExitCode`;
    return await new Promise((resolve) => {
      const child = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps], { windowsHide: true });
      let err = "";
      child.stderr?.on("data", (d) => (err += d.toString()));
      child.on("error", (e) => resolve({ ok: false, error: e.message }));
      child.on("close", (code) =>
        resolve(code === 0 ? { ok: true } : { ok: false, error: /canceled|cancelled|1223/i.test(err) ? "Installation was cancelled." : `Setup ended with code ${code}. ${err.trim()}` }),
      );
    });
  });
}

// Without a usable GPU (remote desktop, VMs) Chromium refuses WebGL unless the SwiftShader
// software fallback is allowed. The app only renders its own localhost content.
app.commandLine.appendSwitch("enable-unsafe-swiftshader");

// One AeroBench at a time: a second launch (e.g. a double click while the first is still
// starting) brings the existing window forward instead of starting another engine.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const w = win && !win.isDestroyed() && win.isVisible() ? win : splash;
    if (w && !w.isDestroyed()) {
      if (w.isMinimized()) w.restore();
      w.focus();
    }
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    showSplash();
    registerIpc();
    splashStatus("Starting the solver engine…");
    try {
      await startEngine();
    } catch (err) {
      splashStatus("The engine did not start.", true);
      dialog.showErrorBox(
        "AeroBench could not start its engine",
        `${(err as Error).message}\n\nRun the AeroBench setup again to repair the installation.`,
      );
      app.quit();
      return;
    }
    splashStatus("Opening the workspace…");
    try {
      await createWindow();
    } catch (err) {
      splashStatus("The workspace did not open.", true);
      dialog.showErrorBox("AeroBench could not open its window", `${(err as Error).message}\n\nRestart AeroBench. If this keeps happening, run the AeroBench setup again.`);
      app.quit();
    }
  });
}

app.on("window-all-closed", () => {
  // Solver jobs and the project worker are detached processes and keep running.
  if (engine) engine.kill();
  app.quit();
});

app.on("before-quit", () => {
  if (engine) engine.kill();
});
