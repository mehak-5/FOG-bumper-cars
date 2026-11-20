// Starts and stops the Python backend. Deliberately free of any `electron` import so it can be
// unit-tested with plain Node.
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";

export interface BackendCommand {
  command: string;
  args: string[];
  cwd: string;
}

export function resolveBackendCommand(opts: {
  isPackaged: boolean;
  resourcesPath: string;
  repoRoot: string;
  env: NodeJS.ProcessEnv;
}): BackendCommand {
  if (opts.isPackaged) {
    const dir = path.join(opts.resourcesPath, "backend");
    return { command: path.join(dir, "bumper-backend.exe"), args: [], cwd: dir };
  }
  let command = opts.env.BUMPER_PYTHON;
  if (!command) {
    const winVenv = path.join(opts.repoRoot, ".venv", "Scripts", "python.exe");
    const posixVenv = path.join(opts.repoRoot, ".venv", "bin", "python");
    if (fs.existsSync(winVenv)) {
      command = winVenv;
    } else if (fs.existsSync(posixVenv)) {
      command = posixVenv;
    } else {
      command = "python";
    }
  }
  return {
    command,
    args: ["-m", "backend.app.main"],
    cwd: opts.repoRoot,
  };
}

export const baseUrl = (port: number): string => `http://127.0.0.1:${port}`;

type FetchLike = (url: string, init?: { method?: string; signal?: AbortSignal }) => Promise<{
  ok: boolean;
  json(): Promise<unknown>;
}>;

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** Poll /api/health until the backend answers {status: "ok"}. Rejects on timeout or abort. */
export async function waitForHealth(
  port: number,
  opts: { timeoutMs?: number; intervalMs?: number; fetchFn?: FetchLike; aborted?: () => boolean } = {},
): Promise<void> {
  const { timeoutMs = 30_000, intervalMs = 250, fetchFn = fetch as unknown as FetchLike } = opts;
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (opts.aborted?.()) throw new Error("Backend exited before it became ready");
    try {
      const res = await fetchFn(`${baseUrl(port)}/api/health`, { signal: AbortSignal.timeout(2000) });
      const body = (await res.json()) as { status?: string };
      if (res.ok && body.status === "ok") return;
    } catch {
      /* not up yet */
    }
    if (Date.now() > deadline) throw new Error(`Backend did not become ready within ${timeoutMs / 1000}s`);
    await sleep(intervalMs);
  }
}

/** Ask the backend whether a ride is being recorded. Unknown (backend unreachable) => false. */
export async function isRecording(port: number, fetchFn: FetchLike = fetch as unknown as FetchLike): Promise<boolean> {
  try {
    const res = await fetchFn(`${baseUrl(port)}/api/health`, { signal: AbortSignal.timeout(1500) });
    return ((await res.json()) as { ride?: string }).ride === "recording";
  } catch {
    return false;
  }
}

export class BackendProcess extends EventEmitter {
  private child: ChildProcess | null = null;
  private exited = false;

  constructor(
    private readonly cmd: BackendCommand,
    private readonly port: number,
    private readonly fetchFn: FetchLike = fetch as unknown as FetchLike,
  ) {
    super();
  }

  get hasExited(): boolean {
    return this.exited;
  }

  start(extraEnv: NodeJS.ProcessEnv = {}): void {
    const child = spawn(this.cmd.command, this.cmd.args, {
      cwd: this.cmd.cwd,
      env: { ...process.env, ...extraEnv },
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child = child;
    child.stdout?.on("data", (d: Buffer) => process.stdout.write(`[backend] ${d}`));
    child.stderr?.on("data", (d: Buffer) => process.stderr.write(`[backend] ${d}`));
    child.on("error", (err) => {
      this.exited = true;
      this.emit("exit", null, err.message);
    });
    child.on("exit", (code) => {
      this.exited = true;
      this.emit("exit", code, null);
    });
  }

  /**
   * Graceful stop: ask the backend to quit (it stops any active ride and finalizes the videos),
   * wait for it to exit, and only then kill the process tree.
   */
  async stop(graceMs = 20_000): Promise<void> {
    const child = this.child;
    if (!child || this.exited) return;
    const done = new Promise<void>((resolve) => this.once("exit", () => resolve()));
    try {
      await this.fetchFn(`${baseUrl(this.port)}/api/app/quit`, {
        method: "POST",
        signal: AbortSignal.timeout(3000),
      });
    } catch {
      /* backend unreachable - fall through to the kill below */
    }
    await Promise.race([done, sleep(graceMs)]);
    if (!this.exited) {
      this.killTree(child);
      await Promise.race([done, sleep(3000)]); // the OS reports the exit a moment later
    }
  }

  private killTree(child: ChildProcess): void {
    if (child.pid === undefined) return;
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
    } else {
      child.kill("SIGKILL");
    }
  }
}
