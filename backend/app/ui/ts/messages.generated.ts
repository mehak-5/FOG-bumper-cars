// AUTO-GENERATED from backend/app/core/models.py by tools/gen_ts_types.py - DO NOT EDIT.
// Run `python tools/gen_ts_types.py` after changing the Pydantic models.

export interface AppState {
  venue: string;
  ride: "idle" | "starting" | "recording" | "stopping";
  session_id: string | null;
  started_at: string | null;
  cameras: CameraState[];
  last_session: LastSession | null;
  readiness: Record<string, CameraReadiness>;
  trigger: TriggerState | null;
  ride_ends_at: number | null;
  disk_free_gb: number | null;
  notes: string;
}

export interface CameraFeedRequest {
  camera_id: string;
}

export interface CameraReadiness {
  ready: boolean;
  reason: string;
  last_frame_at?: number | null;
}

export interface CameraReadinessMsg {
  readiness: Record<string, CameraReadiness>;
}

export interface CameraState {
  id: string;
  name: string;
  status: "idle" | "connecting" | "recording" | "completed" | "failed" | "unavailable";
  frames: number;
  file: string | null;
  error: string | null;
}

export interface DiskStatus {
  free_gb: number;
  warn: boolean;
}

export interface Empty {
}

export interface ErrorMsg {
  message: string;
  code?: string | null;
}

export interface LastSession {
  id: string;
  status: string;
}

export interface LogEntry {
  level: string;
  logger: string;
  message: string;
  ts: string;
}

export interface Notice {
  level: "info" | "warning" | "error";
  text: string;
}

export interface PreviewFrame {
  camera_id: string;
  data_url?: string | null;
}

export interface ProcessingProgress {
  session_id: string;
  status: string;
  percent?: number | null;
}

export interface ReelReady {
  session_id: string;
}

export interface SessionNotes {
  notes: string;
}

export interface ShareCreated {
  session_id: string;
  url: string;
  expires_at: string;
}

export interface TriggerState {
  source: "keyboard" | "pcb" | "button";
  last_key: string | null;
  last_accepted: boolean | null;
  last_reason: string | null;
}

export interface TunnelStatus {
  up: boolean;
  url?: string | null;
}

export interface ClientPayloads {
  "state.get": Empty;
  "ride.start": Empty;
  "ride.end": Empty;
  "session.notes": SessionNotes;
  "logs.subscribe": Empty;
  "logs.unsubscribe": Empty;
  "ping": Empty;
  "preview.open": CameraFeedRequest;
  "preview.close": CameraFeedRequest;
}

export interface ServerPayloads {
  "state": AppState;
  "notice": Notice;
  "preview.frame": PreviewFrame;
  "camera.readiness": CameraReadinessMsg;
  "disk.status": DiskStatus;
  "log": LogEntry;
  "processing.progress": ProcessingProgress;
  "reel.ready": ReelReady;
  "share.created": ShareCreated;
  "tunnel.status": TunnelStatus;
  "app.quit": Empty;
  "error": ErrorMsg;
  "pong": Empty;
}

export interface Envelope<T extends string, P> {
  type: T;
  id: string;
  ts: string;
  payload: P;
}

export type ClientMessageType = keyof ClientPayloads;
export type ServerMessageType = keyof ServerPayloads;
export type ClientMessage = {
  [K in ClientMessageType]: Envelope<K, ClientPayloads[K]>;
}[ClientMessageType];
export type ServerMessage = {
  [K in ServerMessageType]: Envelope<K, ServerPayloads[K]>;
}[ServerMessageType];
