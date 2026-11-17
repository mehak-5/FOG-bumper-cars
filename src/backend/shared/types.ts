export type CameraStatus = 'idle' | 'connecting' | 'recording' | 'completed' | 'failed' | 'unavailable';
export type RideStatus = 'idle' | 'starting' | 'recording' | 'stopping';

export interface CameraState {
  id: string;
  name: string;
  status: CameraStatus;
  frames: number;
  file: string | null; // relative to the session folder
  error: string | null;
}

export type CameraReadiness = { ready: boolean; reason: string; lastFrameAt: number | null };

export interface TriggerState {
  source: 'keyboard' | 'pcb' | 'button';
  lastKey: string | null;
  lastAccepted: boolean | null;
  lastReason: string | null;
}

export interface AppState {
  venue: string;
  ride: RideStatus;
  sessionId: string | null;
  startedAt: string | null;
  cameras: CameraState[];
  lastSession: { id: string; status: string } | null;
  readiness: Record<string, CameraReadiness>;
  trigger: TriggerState | null;
  rideEndsAt: number | null;   // epoch ms, set when ride starts with ride_duration_seconds
  diskFreeGb: number | null;
  notes: string;               // per-ride notes, empty string by default
}

export interface Notice {
  level: 'info' | 'warning' | 'error';
  text: string;
}

// Health check types
export type HealthStatus = 'pending' | 'checking' | 'pass' | 'fail' | 'skipped';

export interface HealthCheckResult {
  ok: boolean;
  message: string;
  attempts: number;
}

export interface HealthCheckProgress {
  attempt: number;
  maxAttempts: number;
}
