// Persistent preview manager: one lightweight FFmpeg JPEG pipe per enabled camera.
// Keeps pipes alive between rides; auto-reconnects with exponential backoff.
// No electron imports here — only Node.js primitives.
import { spawn, type ChildProcess } from 'node:child_process';
import type { CameraConfig, RecordingConfig, Source } from './config';
import { listWebcams } from './devices';
import { getLogger } from './logger';
import type { CameraReadiness } from '../shared/types';

export type { CameraReadiness };

const log = getLogger('preview');

const FRAME_FRESH_MS = 2000;
const SOI = Buffer.from([0xff, 0xd8]);
const EOI = Buffer.from([0xff, 0xd9]);

// ---------------------------------------------------------------------------
// Helper: build ffmpeg args for a JPEG pipe preview stream (~2 FPS, 320×180)
// ---------------------------------------------------------------------------

export async function previewInputArgs(source: Source, rec: RecordingConfig, inputExtra: string[] = []): Promise<string[]> {
  if (source.type === 'webcam') {
    if (process.platform === 'win32') {
      const name = source.name ?? (await listWebcams(rec.ffmpeg_path))[source.index ?? 0];
      if (!name) throw new Error(`Webcam #${source.index} not found for preview`);
      return [...inputExtra, '-f', 'dshow', '-i', `video=${name}`];
    }
    return [...inputExtra, '-f', 'v4l2', '-i', source.name ?? `/dev/video${source.index}`];
  }
  if (source.type === 'stream') {
    const rtsp = source.url.toLowerCase().startsWith('rtsp');
    const opts = rtsp ? ['-rtsp_transport', 'tcp', '-timeout', String(rec.open_timeout_seconds * 1e6)] : [];
    return [...inputExtra, ...opts, '-i', source.url];
  }
  // file source — real-time looping for dev
  return [...inputExtra, '-re', '-stream_loop', '-1', '-i', source.path];
}

export async function previewArgs(cam: CameraConfig, rec: RecordingConfig): Promise<string[]> {
  const inputPart = await previewInputArgs(cam.source, rec, cam.input_args ?? []);
  return [
    '-hide_banner', '-loglevel', 'error',
    ...inputPart,
    '-vf', 'scale=320:180,fps=2',
    '-f', 'image2pipe',
    '-vcodec', 'mjpeg',
    '-',
  ];
}

// ---------------------------------------------------------------------------
// Per-camera pipe state
// ---------------------------------------------------------------------------

interface PipeState {
  camId: string;
  proc: ChildProcess | null;
  buf: Buffer;
  lastFrame: Buffer | null;
  lastFrameAt: number | null;
  recording: boolean;
  stopped: boolean;         // true after global stop()
  backoff: number;          // next retry delay ms
  retryTimer: NodeJS.Timeout | null;
}

// ---------------------------------------------------------------------------
// PreviewManager
// ---------------------------------------------------------------------------

export class PreviewManager {
  private pipes = new Map<string, PipeState>();

  constructor(
    private cameras: CameraConfig[],
    private rec: RecordingConfig,
  ) {}

  /** Open pipes for all cameras. Call once at app startup. */
  start(): void {
    for (const cam of this.cameras) {
      const state: PipeState = {
        camId: cam.id,
        proc: null,
        buf: Buffer.alloc(0),
        lastFrame: null,
        lastFrameAt: null,
        recording: false,
        stopped: false,
        backoff: 1000,
        retryTimer: null,
      };
      this.pipes.set(cam.id, state);
      this.openPipe(cam, state);
    }
  }

  /** Kill all pipes. Call on shutdown. */
  stop(): void {
    for (const [, state] of this.pipes) {
      state.stopped = true;
      if (state.retryTimer) clearTimeout(state.retryTimer);
      state.proc?.kill();
    }
  }

  /** Returns the latest frame as a base64 JPEG data-URL, or null if none. */
  getFrame(cameraId: string): string | null {
    const state = this.pipes.get(cameraId);
    if (!state?.lastFrame) return null;
    return `data:image/jpeg;base64,${state.lastFrame.toString('base64')}`;
  }

  /** True if a fresh frame arrived within the last 2000 ms. While recording, always returns true. */
  isReady(cameraId: string): boolean {
    const state = this.pipes.get(cameraId);
    if (!state) return false;
    if (state.recording) return true;
    return state.lastFrameAt !== null && Date.now() - state.lastFrameAt < FRAME_FRESH_MS;
  }

  /** Map of cameraId → readiness object. */
  getReadiness(): Record<string, CameraReadiness> {
    const result: Record<string, CameraReadiness> = {};
    for (const [id, state] of this.pipes) {
      if (state.recording) {
        result[id] = { ready: true, reason: 'recording', lastFrameAt: state.lastFrameAt };
      } else if (state.lastFrameAt !== null && Date.now() - state.lastFrameAt < FRAME_FRESH_MS) {
        result[id] = { ready: true, reason: 'live', lastFrameAt: state.lastFrameAt };
      } else if (state.lastFrameAt !== null) {
        result[id] = { ready: false, reason: 'stale frame', lastFrameAt: state.lastFrameAt };
      } else {
        result[id] = { ready: false, reason: 'no frame yet', lastFrameAt: null };
      }
    }
    return result;
  }

  /**
   * Called by RecorderManager before/after a ride so we don't
   * fight with the recorder for the camera device.
   */
  setRecording(cameraId: string, recording: boolean): void {
    const state = this.pipes.get(cameraId);
    if (!state) return;
    const wasRecording = state.recording;
    state.recording = recording;
    if (wasRecording && !recording) {
      // Ride finished — reopen the preview pipe
      const cam = this.cameras.find((c) => c.id === cameraId);
      if (cam && !state.stopped) {
        log.info(`[${cameraId}] recorder released — reopening preview pipe`);
        this.openPipe(cam, state);
      }
    } else if (!wasRecording && recording) {
      // Recorder is taking over — kill our pipe to free the device
      log.info(`[${cameraId}] recorder starting — closing preview pipe`);
      if (state.retryTimer) { clearTimeout(state.retryTimer); state.retryTimer = null; }
      state.proc?.kill();
      state.proc = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Internal: open one pipe and wire up its data/close handlers
  // ---------------------------------------------------------------------------

  private openPipe(cam: CameraConfig, state: PipeState): void {
    if (state.stopped || state.recording) return;

    previewArgs(cam, this.rec).then((args) => {
      if (state.stopped || state.recording) return;
      log.info(`[${cam.id}] opening preview pipe`);
      const proc = spawn(this.rec.ffmpeg_path, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
      state.proc = proc;
      state.buf = Buffer.alloc(0);

      proc.stdout?.on('data', (chunk: Buffer) => this.onData(state, chunk));
      proc.stderr?.on('data', () => {}); // suppress; logged on close if needed
      proc.on('error', (err) => {
        log.warn(`[${cam.id}] preview pipe error: ${err.message}`);
      });
      proc.on('close', (code) => {
        if (state.stopped) return;
        if (!state.recording) {
          log.warn(`[${cam.id}] preview pipe closed (code ${code}) — will retry in ${state.backoff}ms`);
          this.scheduleRetry(cam, state);
        }
      });
    }).catch((err: unknown) => {
      if (state.stopped) return;
      log.warn(`[${cam.id}] could not build preview args: ${(err as Error).message} — will retry`);
      this.scheduleRetry(cam, state);
    });
  }

  private scheduleRetry(cam: CameraConfig, state: PipeState): void {
    if (state.stopped || state.recording) return;
    if (state.retryTimer) return; // already scheduled
    state.retryTimer = setTimeout(() => {
      state.retryTimer = null;
      state.proc = null;
      this.openPipe(cam, state);
    }, state.backoff);
    // Exponential backoff capped at 30 s
    state.backoff = Math.min(state.backoff * 2, 5_000);
  }

  private onData(state: PipeState, chunk: Buffer): void {
    state.buf = Buffer.concat([state.buf, chunk]);
    // Reset backoff on first data
    state.backoff = 1000;

    // Extract all complete JPEG frames from the buffer
    // A MJPEG stream from FFmpeg image2pipe concatenates raw JPEG blobs.
    while (true) {
      const soiIdx = state.buf.indexOf(SOI);
      if (soiIdx === -1) { state.buf = Buffer.alloc(0); break; }
      // Trim anything before the SOI
      if (soiIdx > 0) state.buf = state.buf.subarray(soiIdx);
      // Look for EOI after SOI+2
      const eoiIdx = state.buf.indexOf(EOI, 2);
      if (eoiIdx === -1) break; // partial frame — wait for more data
      // Extract the complete frame (include both SOI and EOI)
      const frame = state.buf.subarray(0, eoiIdx + 2);
      state.lastFrame = Buffer.from(frame);
      state.lastFrameAt = Date.now();
      // Advance buffer past this frame
      state.buf = state.buf.subarray(eoiIdx + 2);
    }
  }
}
