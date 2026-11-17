// Backend entry: wires Trigger node + Recorder node and exposes them to the UI over IPC.
import { spawnSync } from 'node:child_process';
import { join, resolve } from 'node:path';
import { ipcMain } from 'electron';
import { IPC } from './constants';
import { RecorderManager } from './managers/recorderManager';
import { loadConfig } from './services/config';
import { checkInternet, checkMomentsServer } from './services/healthCheck';
import { getLogger, initLogging } from './services/logger';
import { EVENTS, EventBus } from './shared/events';
import { KeyboardTrigger } from './triggers/keyboardTrigger';

export interface Backend {
  shutdown: () => Promise<void>;
  killAll: () => void;
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

  const probe = spawnSync(cfg.recording.ffmpeg_path, ['-version']);
  const fatal =
    probe.error || probe.status !== 0
      ? `ffmpeg not found at '${cfg.recording.ffmpeg_path}'. Install it or set recording.ffmpeg_path / FFMPEG_PATH.`
      : null;
  if (fatal) log.error(fatal);

  const bus = new EventBus();
  const recorder = new RecorderManager(cfg, bus, { fatal });
  const trigger = new KeyboardTrigger(bus, cfg.trigger.debounce_seconds);

  bus.subscribe(EVENTS.STATE_CHANGED, (state) => o.send(IPC.STATE, state));
  bus.subscribe(EVENTS.NOTICE, (notice) => o.send(IPC.NOTICE, notice));

  ipcMain.handle(IPC.GET_STATE, () => recorder.getState());
  ipcMain.handle(IPC.TRIGGER_KEY, async (_event, key: unknown) => {
    const keepRunning = await trigger.handleKey(String(key).toLowerCase());
    if (!keepRunning) o.quit();
  });
  if (fatal) setTimeout(() => void bus.publish(EVENTS.NOTICE, { level: 'error', text: fatal }), 1500);

  // ---- Health check IPC handlers ----
  ipcMain.handle(IPC.HEALTH_CHECK_INTERNET, async (_event, maxAttempts: unknown) => {
    return checkInternet(typeof maxAttempts === 'number' ? maxAttempts : 9);
  });

  ipcMain.handle(IPC.HEALTH_CHECK_SERVER, async (_event, maxAttempts: unknown) => {
    const serverUrl = cfg.venue.moments_server_url ?? 'http://localhost:8080/health';
    return checkMomentsServer(serverUrl, typeof maxAttempts === 'number' ? maxAttempts : 3);
  });

  ipcMain.handle(IPC.HEALTH_CHECK_CAMERAS, () => {
    // Returns the total configured camera count and how many are ready
    const total = cfg.cameras.length;
    return { total, cameras: cfg.cameras.map((c) => ({ id: c.id, name: c.name })) };
  });

  return { shutdown: () => recorder.shutdown(), killAll: () => recorder.killAll() };
}
