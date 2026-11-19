// Operator console: live state, previews, readiness, Start/End buttons, notes and notices.
import type { AppState, CameraReadiness, CameraState, Notice, ServerMessage } from "./messages.generated.js";
import { OperatorSocket, setConnectionBadge } from "./ws-client.js";

const MAX_NOTICES = 20;

function byId<T extends HTMLElement>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

let state: AppState | null = null;
let readiness: Record<string, CameraReadiness> = {};
let timerHandle: number | undefined;

const socket = new OperatorSocket(onMessage, setConnectionBadge, () => socket.send("state.get"));

function onMessage(msg: ServerMessage): void {
  switch (msg.type) {
    case "state":
      state = msg.payload;
      readiness = msg.payload.readiness;
      render();
      break;
    case "camera.readiness":
      readiness = msg.payload.readiness;
      render();
      break;
    case "preview.frame": {
      const img = document.querySelector<HTMLImageElement>(`img[data-camera="${CSS.escape(msg.payload.camera_id)}"]`);
      if (img) img.src = msg.payload.data_url;
      break;
    }
    case "notice":
      addNotice(msg.payload);
      break;
    case "disk.status": {
      const el = byId("disk");
      if (el) {
        el.textContent = `${msg.payload.free_gb.toFixed(1)} GB free`;
        el.classList.toggle("warn", msg.payload.warn);
      }
      break;
    }
    case "app.quit":
      addNotice({ level: "info", text: "Application is closing." });
      break;
    case "error":
      addNotice({ level: "error", text: msg.payload.message });
      break;
    default:
      break;
  }
}

function allReady(): boolean {
  if (!state) return false;
  return state.cameras.every((c) => readiness[c.id]?.ready === true);
}

function render(): void {
  if (!state) return;
  renderBanner(state);
  renderCameras(state.cameras);
  renderButtons(state);
  renderTimer(state);
  const notes = byId<HTMLTextAreaElement>("notes");
  if (notes && document.activeElement !== notes) notes.value = state.notes;
  const disk = byId("disk");
  if (disk && state.disk_free_gb !== null) disk.textContent = `${state.disk_free_gb.toFixed(1)} GB free`;
}

function renderBanner(s: AppState): void {
  const banner = byId("banner");
  if (!banner) return;
  banner.dataset.ride = s.ride;
  const labels: Record<AppState["ride"], string> = {
    idle: "Ready",
    starting: "Starting…",
    recording: "● RECORDING",
    stopping: "Stopping…",
  };
  banner.textContent = labels[s.ride];
  const last = byId("last-session");
  if (last) last.textContent = s.last_session ? `Last ride: ${s.last_session.status}` : "";
}

function renderCameras(cameras: CameraState[]): void {
  for (const cam of cameras) {
    const card = document.querySelector<HTMLElement>(`[data-camera-card="${CSS.escape(cam.id)}"]`);
    if (!card) continue;
    card.dataset.status = cam.status;
    const status = card.querySelector<HTMLElement>(".cam-status");
    const ready = readiness[cam.id];
    if (status) {
      const detail = cam.error ?? (cam.status === "idle" ? (ready?.reason ?? "no frame yet") : `${cam.frames} frames`);
      status.textContent = `${cam.status} · ${detail}`;
    }
    const dot = card.querySelector<HTMLElement>(".ready-dot");
    if (dot) dot.dataset.ready = String(ready?.ready === true);
  }
}

function renderButtons(s: AppState): void {
  const start = byId<HTMLButtonElement>("btn-start");
  const end = byId<HTMLButtonElement>("btn-end");
  const hint = byId("start-hint");
  const ready = allReady();
  if (start) start.disabled = !(s.ride === "idle" && ready);
  if (end) end.disabled = s.ride !== "recording";
  if (hint) hint.textContent = s.ride === "idle" && !ready ? "Waiting for every camera to deliver a fresh frame…" : "";
}

function renderTimer(s: AppState): void {
  const el = byId("ride-timer");
  window.clearInterval(timerHandle);
  if (!el) return;
  if (s.ride !== "recording" || !s.started_at) {
    el.textContent = "";
    return;
  }
  const started = Date.parse(s.started_at);
  const endsAt = s.ride_ends_at;
  const tick = (): void => {
    const elapsed = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const left = endsAt === null ? "" : ` · auto-stop in ${Math.max(0, Math.round((endsAt - Date.now()) / 1000))}s`;
    el.textContent = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}${left}`;
  };
  tick();
  timerHandle = window.setInterval(tick, 500);
}

function addNotice(n: Notice): void {
  const list = byId("notices");
  if (!list) return;
  const li = document.createElement("li");
  li.dataset.level = n.level;
  li.textContent = `${new Date().toLocaleTimeString()}  ${n.text}`; // textContent: never HTML
  list.prepend(li);
  while (list.children.length > MAX_NOTICES) list.lastElementChild?.remove();
}

function wire(): void {
  byId("btn-start")?.addEventListener("click", () => socket.send("ride.start"));
  byId("btn-end")?.addEventListener("click", () => socket.send("ride.end"));
  const notes = byId<HTMLTextAreaElement>("notes");
  let debounce: number | undefined;
  notes?.addEventListener("input", () => {
    window.clearTimeout(debounce);
    debounce = window.setTimeout(() => socket.send("session.notes", { notes: notes.value }), 400);
  });
  socket.connect();
}

wire();
