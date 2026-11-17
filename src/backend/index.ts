// Backend entry: wires Trigger node + Recorder node and exposes them to the UI over IPC.
import { spawnSync } from 'node:child_process';
import { readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { ipcMain, shell } from 'electron';
import YAML from 'yaml';
import { IPC } from './constants';
import { RecorderManager } from './managers/recorderManager';
import { loadConfig } from './services/config';
import { checkInternet, checkMomentsServer } from './services/healthCheck';
import { getLogger, initLogging } from './services/logger';
import { PreviewManager } from './services/preview';
import { checkDisk, recoverInterruptedSessions } from './services/startup';
import { EVENTS, EventBus } from './shared/events';
import { KeyboardTrigger } from './triggers/keyboardTrigger';

export interface Backend {
  shutdown: () => Promise<void>;
  killAll: () => void;
  isRecording: () => boolean;
}

export function startBackend(o: {
  appRoot: string;
  dataRoot: string;
  configPath?: string;
  send: (channel: string, payload: unknown) => void;
  quit: () => void;
}): Backend {
  initLogging(join(o.dataRoot, 'logs'));
  const log = getLogger('backend');

  const configPath = resolve(o.appRoot, o.configPath ?? 'config/venue.yaml');
  const cfg = loadConfig(configPath, o.appRoot, o.dataRoot); // throws a readable error
  log.info(`Venue: ${cfg.venue.name ?? 'unnamed'} | ${cfg.cameras.length} camera(s) | config: ${configPath}`);

  // Try the configured ffmpeg binary first.
  let probe = spawnSync(cfg.recording.ffmpeg_path, ['-version']);
  // If it fails (e.g., not on PATH) fall back to the bundled static binary.
  if (probe.error || probe.status !== 0) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const staticFfmpeg = require('ffmpeg-static') as string;
      if (staticFfmpeg) {
        cfg.recording.ffmpeg_path = staticFfmpeg;
        probe = spawnSync(cfg.recording.ffmpeg_path, ['-version']);
      }
    } catch {
      // If ffmpeg-static is unavailable we keep the original path – fatal will be reported.
    }
  }
  const fatal =
    probe.error || probe.status !== 0
      ? `ffmpeg not found at '${cfg.recording.ffmpeg_path}'. Install it or set recording.ffmpeg_path / FFMPEG_PATH.`
      : null;
  if (fatal) log.error(fatal);

  const bus = new EventBus();

  // Recover any interrupted sessions from a previous crash
  recoverInterruptedSessions(cfg.sessionsDir).then((n) => {
    if (n > 0) log.info(`Recovered ${n} interrupted session(s)`);
  }).catch((err: unknown) => log.error('recoverInterruptedSessions failed', err));

  // Preview manager — no Electron imports
  const previewManager = new PreviewManager(cfg.cameras, cfg.recording);

  const recorder = new RecorderManager(cfg, bus, { fatal, previewManager });
  const trigger = new KeyboardTrigger(bus, cfg.trigger.debounce_seconds, {
    onTriggerState: (ts) => recorder.updateTriggerState(ts),
  });

  bus.subscribe(EVENTS.STATE_CHANGED, (state) => o.send(IPC.STATE, state));
  bus.subscribe(EVENTS.NOTICE, (notice) => o.send(IPC.NOTICE, notice));

  ipcMain.handle(IPC.GET_STATE, () => recorder.getState());
  ipcMain.handle(IPC.TRIGGER_KEY, async (_event, key: unknown) => {
    const keepRunning = await trigger.handleKey(String(key).toLowerCase());
    if (!keepRunning) o.quit();
  });

  // Session notes
  ipcMain.handle(IPC.SESSION_NOTES, (_event, notes: unknown) => {
    recorder.setNotes(String(notes ?? ''));
  });

  // History handlers
  ipcMain.handle(IPC.HISTORY_LIST, async () => {
    try {
      const entries = await readdir(cfg.sessionsDir);
      const results: unknown[] = [];
      for (const name of entries) {
        if (!name.startsWith('ride_')) continue;
        try {
          const raw = await readFile(join(cfg.sessionsDir, name, 'session.json'), 'utf8');
          results.push(JSON.parse(raw));
        } catch { /* skip corrupt entries */ }
      }
      return results;
    } catch { return []; }
  });

  ipcMain.handle(IPC.HISTORY_DELETE, async (_event, sessionId: unknown) => {
    const name = String(sessionId ?? '');
    if (!name.startsWith('ride_')) throw new Error(`Invalid session id: ${name}`);
    await rm(join(cfg.sessionsDir, name), { recursive: true, force: true });
  });

  ipcMain.handle(IPC.HISTORY_OPEN_FOLDER, async (_event, sessionId: unknown) => {
    const name = String(sessionId ?? '');
    if (!name.startsWith('ride_')) throw new Error(`Invalid session id: ${name}`);
    await shell.openPath(join(cfg.sessionsDir, name));
  });

  // Config handlers — read/write venue.yaml using the yaml Document API
  ipcMain.handle(IPC.CONFIG_CAMERAS_GET, async () => {
    try {
      const raw = await readFile(configPath, 'utf8');
      const doc = YAML.parseDocument(raw);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return (doc.get('cameras') as any) ?? [];
    } catch (err) { throw new Error(`Cannot read config: ${(err as Error).message}`); }
  });

  ipcMain.handle(IPC.CONFIG_CAMERAS_SAVE, async (_event, cameras: unknown) => {
    const raw = await readFile(configPath, 'utf8');
    const doc = YAML.parseDocument(raw);
    doc.set('cameras', cameras);
    await writeFile(configPath, doc.toString());
  });

  ipcMain.handle(IPC.CONFIG_SETTINGS_GET, async () => {
    try {
      const raw = await readFile(configPath, 'utf8');
      const doc = YAML.parseDocument(raw);
      return {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ...(doc.get('recording') as any ?? {}),
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        trigger: doc.get('trigger') as any ?? {},
      };
    } catch (err) { throw new Error(`Cannot read config: ${(err as Error).message}`); }
  });

  ipcMain.handle(IPC.CONFIG_SETTINGS_SAVE, async (_event, settings: unknown) => {
    const s = settings as Record<string, unknown>;
    const raw = await readFile(configPath, 'utf8');
    const doc = YAML.parseDocument(raw);
    const { trigger, ...recording } = s;
    doc.set('recording', recording);
    if (trigger !== undefined) doc.set('trigger', trigger);
    await writeFile(configPath, doc.toString());
  });

  // ---- Health check IPC handlers ----
  ipcMain.handle(IPC.HEALTH_CHECK_INTERNET, async (_event, maxAttempts: unknown) => {
    return checkInternet(typeof maxAttempts === 'number' ? maxAttempts : 9);
  });

  ipcMain.handle(IPC.HEALTH_CHECK_SERVER, async (_event, maxAttempts: unknown) => {
    const serverUrl = cfg.venue.moments_server_url ?? 'http://localhost:8080/health';
    return checkMomentsServer(serverUrl, typeof maxAttempts === 'number' ? maxAttempts : 3);
  });

  ipcMain.handle(IPC.HEALTH_CHECK_CAMERAS, () => {
    const total = cfg.cameras.length;
    return { total, cameras: cfg.cameras.map((c) => ({ id: c.id, name: c.name })) };
  });

  if (fatal) setTimeout(() => void bus.publish(EVENTS.NOTICE, { level: 'error', text: fatal }), 1500);

  // Start preview manager after recorder is set up
  if (!fatal) previewManager.start();

  // Push preview frames to UI every 100 ms
  const previewInterval = setInterval(() => {
    for (const cam of cfg.cameras) {
      const dataUrl = previewManager.getFrame(cam.id);
      if (dataUrl) o.send(IPC.PREVIEW_FRAME, { cameraId: cam.id, dataUrl });
    }
  }, 100);

  // Push readiness to UI every 500 ms
  let lastReadinessJson = '';
  const readinessInterval = setInterval(() => {
    const readiness = previewManager.getReadiness();
    const json = JSON.stringify(readiness);
    if (json !== lastReadinessJson) {
      lastReadinessJson = json;
      o.send(IPC.READINESS, readiness);
    }
  }, 500);

  // Disk check at startup and every 60 s
  const pushDisk = () => {
    checkDisk(cfg.sessionsDir, cfg.diskWarnGb).then(({ freeGb, warn }) => {
      recorder.updateDiskStatus(freeGb);
      o.send(IPC.DISK_STATUS, { freeGb });
      if (warn) void bus.publish(EVENTS.NOTICE, { level: 'warning', text: `Low disk space: ${freeGb.toFixed(1)} GB free` });
    }).catch((err: unknown) => log.warn('disk check failed', err));
  };
  pushDisk();
  const diskInterval = setInterval(pushDisk, 60_000);

  return {
    shutdown: async () => {
      clearInterval(previewInterval);
      clearInterval(readinessInterval);
      clearInterval(diskInterval);
      previewManager.stop();
      await recorder.shutdown();
    },
    killAll: () => recorder.killAll(),
    isRecording: () => recorder.getState().ride === 'recording',
  };
}
