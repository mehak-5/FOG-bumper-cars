import type { AppState, CameraReadiness, HealthCheckResult, Notice } from '../../backend/shared/types';

type CameraConfigRaw = { id: string; name: string; source: string | number; enabled: boolean };
type SessionMeta = {
  sessionId: string;
  status: string;
  startTime: string;
  endTime: string | null;
  duration: number | null;
  cameras: unknown[];
};

declare global {
  interface Window {
    bumper: {
      // state
      getState(): Promise<AppState>;
      pressKey(key: string): Promise<void>;
      onState(cb: (s: AppState) => void): () => void;
      onNotice(cb: (n: Notice) => void): () => void;

      // previews & readiness
      onPreviewFrame(cb: (data: { cameraId: string; dataUrl: string }) => void): () => void;
      onReadiness(cb: (r: Record<string, CameraReadiness>) => void): () => void;
      onDiskStatus(cb: (d: { freeGb: number }) => void): () => void;

      // session
      setNotes(notes: string): Promise<void>;

      // config
      getCameras(): Promise<CameraConfigRaw[]>;
      saveCameras(cameras: CameraConfigRaw[]): Promise<void>;
      getSettings(): Promise<unknown>;
      saveSettings(s: unknown): Promise<void>;

      // history
      listHistory(): Promise<SessionMeta[]>;
      deleteSession(id: string): Promise<void>;
      openSessionFolder(id: string): Promise<void>;

      // health checks
      checkInternet(maxAttempts?: number): Promise<HealthCheckResult>;
      checkServer(maxAttempts?: number): Promise<HealthCheckResult>;
      getCameraConfig(): Promise<{ total: number; cameras: { id: string; name: string }[] }>;
    };
  }
}
export {};
