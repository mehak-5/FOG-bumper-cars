// Live log viewer: subscribes to the log stream and appends lines (capped).
import type { LogEntry, ServerMessage } from "./messages.generated.js";
import { OperatorSocket, setConnectionBadge } from "./ws-client.js";

const MAX_LINES = 1000;
const LEVELS = ["debug", "info", "warning", "error", "critical"];

const out = document.getElementById("log-lines") as HTMLElement | null;
const pause = document.getElementById("log-pause") as HTMLInputElement | null;
const filter = document.getElementById("log-level") as HTMLSelectElement | null;
let held: LogEntry[] = [];

const socket = new OperatorSocket(onMessage, setConnectionBadge, () => socket.send("logs.subscribe"));

function visible(entry: LogEntry): boolean {
  const min = LEVELS.indexOf(filter?.value ?? "info");
  return LEVELS.indexOf(entry.level) >= min;
}

function append(entry: LogEntry): void {
  if (!out || !visible(entry)) return;
  const line = document.createElement("div");
  line.dataset.level = entry.level;
  line.textContent = `${entry.ts.slice(11, 19)} ${entry.level.toUpperCase().padEnd(7)} [${entry.logger}] ${entry.message}`;
  out.append(line);
  while (out.childElementCount > MAX_LINES) out.firstElementChild?.remove();
  if (!pause?.checked) out.scrollTop = out.scrollHeight;
}

function onMessage(msg: ServerMessage): void {
  if (msg.type === "log") {
    if (pause?.checked) held.push(msg.payload);
    else append(msg.payload);
  }
}

pause?.addEventListener("change", () => {
  if (pause.checked) return;
  for (const e of held) append(e);
  held = [];
});

socket.connect();
window.addEventListener("beforeunload", () => socket.close());
