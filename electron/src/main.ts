// Thin desktop shell: single-instance lock, spawn the Python backend, wait for /api/health, open
// a window on http://127.0.0.1:<port>/. Nothing else.
import fs from "node:fs";
import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain } from "electron";
import {
  BackendProcess,
  baseUrl,
  isRecording,
  resolveBackendCommand,
  waitForHealth,
} from "./backend-process";

const PORT = Number(process.env.BUMPER_PORT ?? 8000); // must equal server.port in venue.yaml
const REPO_ROOT = path.resolve(__dirname, "..", "..");

let win: BrowserWindow | null = null;
let backend: BackendProcess | null = null;
let quitting = false;

function createWindow(): BrowserWindow {
  const w = new BrowserWindow({
    width: 1200,
    height: 800,
    title: "Bumper Car Moments",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  const origin = baseUrl(PORT);
  // Never navigate away from, or open windows onto, anything but our own backend.
  w.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  w.webContents.on("will-navigate", (event, url) => {
    if (new URL(url).origin !== origin) event.preventDefault();
  });
  void w.loadURL(`${origin}/`);
  w.on("close", (event) => {
    if (quitting) return;
    event.preventDefault();
    void confirmClose(w);
  });
  w.on("closed", () => (win = null));
  return w;
}

async function confirmClose(w: BrowserWindow): Promise<void> {
  if (await isRecording(PORT)) {
    const { response } = await dialog.showMessageBox(w, {
      type: "warning",
      buttons: ["Stop & save ride, then quit", "Cancel"],
      defaultId: 0,
      cancelId: 1,
      title: "Ride in progress",
      message: "A ride is currently recording. Stop and save it before closing?",
    });
    if (response === 1) return;
  }
  app.quit();
}

function patchEnvironment(repoRoot: string): void {
  const home = process.env.USERPROFILE || process.env.HOME || "";
  const sep = path.delimiter;
  const currentPath = process.env.PATH || "";
  const extras = [
    path.join(repoRoot, ".venv", process.platform === "win32" ? "Scripts" : "bin"),
    path.join(home, ".local", "bin"),
    path.join(home, ".cargo", "bin"),
  ];
  const additions = extras.filter((p) => fs.existsSync(p) && !currentPath.includes(p));
  if (additions.length > 0) {
    process.env.PATH = [...additions, currentPath].join(sep);
  }
}

async function boot(): Promise<void> {
  patchEnvironment(REPO_ROOT);
  const cmd = resolveBackendCommand({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot: REPO_ROOT,
    env: process.env,
  });
  console.log(`[desktop] Spawning backend: ${cmd.command} ${cmd.args.join(" ")} (cwd: ${cmd.cwd})`);
  backend = new BackendProcess(cmd, PORT);
  backend.on("exit", (code: number | null, error: string | null) => {
    if (quitting) return;
    dialog.showErrorBox(
      "Bumper Car Moments",
      `The backend stopped${code === null ? "" : ` (exit code ${code})`}${error ? `: ${error}` : ""}.`,
    );
    quitting = true;
    app.quit();
  });
  backend.start();
  try {
    await waitForHealth(PORT, { aborted: () => backend?.hasExited ?? false });
  } catch (err) {
    quitting = true;
    dialog.showErrorBox("Bumper Car Moments - startup error", (err as Error).message);
    await backend.stop(2000);
    app.exit(1);
    return;
  }
  win = createWindow();
}

if (!app.requestSingleInstanceLock()) {
  app.quit(); // two instances would fight over the same cameras
} else {
  app.on("second-instance", () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  });
  ipcMain.handle("desktop:quit", () => app.quit());

  app.on("before-quit", (event) => {
    quitting = true;
    if (!backend || backend.hasExited) return;
    event.preventDefault();
    const b = backend;
    backend = null;
    void b.stop().finally(() => app.quit());
  });
  app.on("window-all-closed", () => app.quit());
  void app.whenReady().then(boot);
}
