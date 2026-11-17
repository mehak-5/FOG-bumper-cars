import { contextBridge, ipcRenderer } from 'electron';
import { IPC } from './backend/constants';
import type { AppState, CameraReadiness, HealthCheckResult, Notice } from './backend/shared/types';

type CameraConfigRaw = { id: string; name: string; source: string | number; enabled: boolean };
type SessionMeta = {
  sessionId: string;
  status: string;
  startTime: string;
  endTime: string | null;
  duration: number | null;
  cameras: unknown[];
};

const subscribe = <T>(channel: string, cb: (data: T) => void) => {
  const handler = (_e: unknown, data: T) => cb(data);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
};

contextBridge.exposeInMainWorld('bumper', {
  // state
  getState: (): Promise<AppState> => ipcRenderer.invoke(IPC.GET_STATE),
  pressKey: (key: string): Promise<void> => ipcRenderer.invoke(IPC.TRIGGER_KEY, key),
  onState: (cb: (s: AppState) => void) => subscribe(IPC.STATE, cb),
  onNotice: (cb: (n: Notice) => void) => subscribe(IPC.NOTICE, cb),

  // previews & readiness
  onPreviewFrame: (cb: (data: { cameraId: string; dataUrl: string }) => void) =>
    subscribe(IPC.PREVIEW_FRAME, cb),
  onReadiness: (cb: (r: Record<string, CameraReadiness>) => void) =>
    subscribe(IPC.READINESS, cb),
  onDiskStatus: (cb: (d: { freeGb: number }) => void) =>
    subscribe(IPC.DISK_STATUS, cb),

  // session
  setNotes: (notes: string): Promise<void> => ipcRenderer.invoke(IPC.SESSION_NOTES, notes),

  // config
  getCameras: (): Promise<CameraConfigRaw[]> => ipcRenderer.invoke(IPC.CONFIG_CAMERAS_GET),
  saveCameras: (cameras: CameraConfigRaw[]): Promise<void> =>
    ipcRenderer.invoke(IPC.CONFIG_CAMERAS_SAVE, cameras),
  getSettings: (): Promise<unknown> => ipcRenderer.invoke(IPC.CONFIG_SETTINGS_GET),
  saveSettings: (s: unknown): Promise<void> => ipcRenderer.invoke(IPC.CONFIG_SETTINGS_SAVE, s),

  // history
  listHistory: (): Promise<SessionMeta[]> => ipcRenderer.invoke(IPC.HISTORY_LIST),
  deleteSession: (id: string): Promise<void> => ipcRenderer.invoke(IPC.HISTORY_DELETE, id),
  openSessionFolder: (id: string): Promise<void> => ipcRenderer.invoke(IPC.HISTORY_OPEN_FOLDER, id),

  // health checks
  checkInternet: (maxAttempts?: number): Promise<HealthCheckResult> =>
    ipcRenderer.invoke(IPC.HEALTH_CHECK_INTERNET, maxAttempts ?? 9),
  checkServer: (maxAttempts?: number): Promise<HealthCheckResult> =>
    ipcRenderer.invoke(IPC.HEALTH_CHECK_SERVER, maxAttempts ?? 3),
  getCameraConfig: (): Promise<{ total: number; cameras: { id: string; name: string }[] }> =>
    ipcRenderer.invoke(IPC.HEALTH_CHECK_CAMERAS),
});
