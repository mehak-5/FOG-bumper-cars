// Loads and validates config/venue.yaml.
import { readFileSync } from 'node:fs';
import { isAbsolute, resolve } from 'node:path';
import YAML from 'yaml';

export type Source =
  | { type: 'webcam'; index?: number; name?: string }
  | { type: 'stream'; url: string }
  | { type: 'file'; path: string };

export interface CameraConfig {
  id: string;
  name: string;
  source: Source;
  input_args?: string[];
}

export interface RecordingConfig {
  ffmpeg_path: string;
  width: number;
  height: number;
  fps: number;
  format: string;
  video_codec: string;
  encoder_args: string[];
  ride_duration_seconds: number | null;
  max_duration_seconds: number | null;
  open_timeout_seconds: number;
  disconnect_timeout_seconds: number;
}

export interface AppConfig {
  venue: { name?: string; moments_server_url?: string };
  recording: RecordingConfig;
  trigger: { debounce_seconds: number };
  sessionsDir: string;
  cameras: CameraConfig[];
  diskWarnGb: number;
}

const DEFAULTS: RecordingConfig = {
  ffmpeg_path: 'ffmpeg', width: 1280, height: 720, fps: 25, format: 'mp4', video_codec: 'libx264',
  encoder_args: ['-preset', 'veryfast', '-crf', '23'], ride_duration_seconds: null,
  max_duration_seconds: 600, open_timeout_seconds: 10, disconnect_timeout_seconds: 5,
};

export function loadConfig(path: string, appRoot: string, dataRoot: string): AppConfig {
  const abs = (base: string, p: string) => (isAbsolute(p) ? p : resolve(base, p));
  const normalize = (raw: unknown): Source => {
    const text = String(raw).trim();
    if (/^\d+$/.test(text)) return { type: 'webcam', index: Number(text) };
    if (/^[a-z]+:\/\//i.test(text)) return { type: 'stream', url: text };
    if (/\.(mp4|mkv|avi|mov|ts)$/i.test(text)) return { type: 'file', path: abs(appRoot, text) };
    return { type: 'webcam', name: text };
  };

  let raw: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  try {
    raw = YAML.parse(readFileSync(path, 'utf8')) ?? {};
  } catch (err) {
    throw new Error(`Cannot read config ${path}: ${(err as Error).message}`);
  }

  const recording: RecordingConfig = { ...DEFAULTS, ...(raw.recording ?? {}) };
  if (process.env.FFMPEG_PATH) recording.ffmpeg_path = process.env.FFMPEG_PATH;
  for (const k of ['width', 'height', 'fps', 'open_timeout_seconds', 'disconnect_timeout_seconds'] as const) {
    if (!(recording[k] > 0)) throw new Error(`recording.${k} must be a positive number`);
  }

  const all: any[] = raw.cameras ?? []; // eslint-disable-line @typescript-eslint/no-explicit-any
  const ids = new Set<string>();
  for (const c of all) {
    for (const f of ['id', 'name', 'source']) {
      if (c[f] === undefined || c[f] === '') throw new Error(`Camera ${JSON.stringify(c)} is missing '${f}'`);
    }
    if (!/^[\w-]+$/.test(String(c.id))) throw new Error(`Camera id '${c.id}' may only contain letters, digits, _ and -`);
    if (ids.has(String(c.id))) throw new Error(`Camera id '${c.id}' is not unique`);
    ids.add(String(c.id));
  }

  const cameras: CameraConfig[] = all
    .filter((c) => c.enabled !== false)
    .map((c) => ({ id: String(c.id), name: String(c.name), input_args: c.input_args, source: normalize(c.source) }));
  if (!cameras.length) throw new Error('No enabled cameras in configuration');

  // Two recorders on one device/stream would compete for the connection.
  const seen = new Set<string>();
  for (const { id, source } of cameras) {
    if (source.type === 'file') continue;
    const key = source.type === 'stream' ? source.url : `webcam:${source.index ?? source.name}`;
    if (seen.has(key)) throw new Error(`Camera '${id}' uses the same source as another enabled camera`);
    seen.add(key);
  }

  return {
    venue: raw.venue ?? {},
    recording,
    trigger: { debounce_seconds: 0.5, ...(raw.trigger ?? {}) },
    sessionsDir: abs(dataRoot, raw.storage?.sessions_dir ?? 'storage/sessions'),
    cameras,
    diskWarnGb: raw.storage?.disk_warn_gb ?? 5,
  };
}
