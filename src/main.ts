import path from 'node:path';
import { spawn, ChildProcess } from 'node:child_process';
import { app, BrowserWindow, dialog } from 'electron';

let win: BrowserWindow | null = null;
let backendProc: ChildProcess | null = null;

const BACKEND_PORT = 8000;
const HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/api/health`;

async function pollHealth(timeoutMs = 15000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(HEALTH_URL);
      if (res.ok) return true;
    } catch {}
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function startPythonBackend() {
  const isWin = process.platform === 'win32';
  const pythonBin = isWin
    ? path.join(app.getAppPath(), '.venv', 'Scripts', 'python.exe')
    : path.join(app.getAppPath(), '.venv', 'bin', 'python');

  console.log(`Starting FastAPI backend using: ${pythonBin}`);
  backendProc = spawn(
    pythonBin,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
    {
      cwd: path.join(app.getAppPath(), 'backend'),
      stdio: 'inherit',
      env: { ...process.env, PYTHONPATH: path.join(app.getAppPath(), 'backend') },
    }
  );

  backendProc.on('error', (err) => {
    console.error('Failed to start FastAPI backend:', err);
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 760,
    title: 'Bumper Car Moments — Operator',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  win.loadURL(`http://127.0.0.1:${BACKEND_PORT}`);
  win.on('closed', () => (win = null));
}

app.whenReady().then(async () => {
  startPythonBackend();

  const isHealthy = await pollHealth();
  if (!isHealthy) {
    dialog.showErrorBox(
      'Backend Startup Failure',
      `FastAPI backend could not be reached at ${HEALTH_URL}. Check console logs.`
    );
    app.exit(1);
    return;
  }

  createWindow();
});

app.on('before-quit', () => {
  if (backendProc) {
    console.log('Stopping Python backend...');
    backendProc.kill();
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
