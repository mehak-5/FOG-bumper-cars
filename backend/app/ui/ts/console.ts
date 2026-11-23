// Operator console: live state, previews, readiness, Start/End buttons, notes and notices.
import type { AppState, CameraReadiness, CameraState, Notice, ServerMessage } from "./messages.generated.js";
import { OperatorSocket, setConnectionBadge } from "./ws-client.js";

const MAX_NOTICES = 20;

// 1x1 transparent GIF. Swapping the <img> to this reliably aborts the running MJPEG request.
const BLANK_GIF = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";

function byId<T extends HTMLElement>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

let state: AppState | null = null;
let readiness: Record<string, CameraReadiness> = {};
let timerHandle: number | undefined;
let lastRideState: string = "idle";
const activeFeeds: Set<string> = new Set();

const socket = new OperatorSocket(onMessage, setConnectionBadge, () => socket.send("state.get"));

// ---------------- camera preview (HTTP MJPEG) ----------------
function camImg(id: string): HTMLImageElement | null {
  return document.querySelector<HTMLImageElement>(`img[data-camera="${CSS.escape(id)}"]`);
}

function startCameraStream(id: string): void {
  const img = camImg(id);
  if (!img) return;
  // Timestamp forces the browser to open a fresh connection every time.
  img.src = `/api/cameras/${encodeURIComponent(id)}/stream?t=${Date.now()}`;
  activeFeeds.add(id);
  if (state) renderFeedButtons(state);
}

function stopCameraStream(id: string): void {
  const img = camImg(id);
  if (img) {
    img.src = BLANK_GIF; // drops the HTTP connection to the server
    img.removeAttribute("src");
  }
  activeFeeds.delete(id);
  if (state) renderFeedButtons(state);
}

function onMessage(msg: ServerMessage): void {
  switch (msg.type) {
    case "state":
      if ((msg.payload.ride === "starting" || msg.payload.ride === "recording") && lastRideState === "idle") {
        const noticesEl = byId("notices");
        if (noticesEl) noticesEl.innerHTML = "";
      }
      lastRideState = msg.payload.ride;
      state = msg.payload;
      readiness = msg.payload.readiness;
      render();
      break;
    case "camera.readiness":
      readiness = msg.payload.readiness;
      render();
      break;
    case "preview.frame":
      // Deprecated: video frames now arrive over HTTP (/api/cameras/{id}/stream) straight into <img>.
      break;
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
  return state.cameras.every((c) => readiness[c.id]?.ready !== false);
}

function render(): void {
  if (!state) return;
  // A ride needs exclusive camera access: drop every open preview stream.
  if (state.ride !== "idle" && activeFeeds.size > 0) {
    [...activeFeeds].forEach(stopCameraStream);
  }
  renderBanner(state);
  renderCameras(state.cameras);
  renderButtons(state);
  renderFeedButtons(state);
  renderTimer(state);
  const notes = byId<HTMLTextAreaElement>("notes");
  if (notes && document.activeElement !== notes) notes.value = state.notes;
  const disk = byId("disk");
  if (disk && state.disk_free_gb !== null) disk.textContent = `${state.disk_free_gb.toFixed(1)} GB free`;
}

function renderFeedButtons(s: AppState): void {
  const isRecording = s.ride !== "idle";
  document.querySelectorAll<HTMLButtonElement>(".btn-open-feed").forEach((btn) => {
    const cid = btn.dataset.cameraId;
    if (!cid) return;
    const isOpen = activeFeeds.has(cid);
    btn.disabled = isRecording || isOpen;
  });
  document.querySelectorAll<HTMLButtonElement>(".btn-close-feed").forEach((btn) => {
    const cid = btn.dataset.cameraId;
    if (!cid) return;
    const isOpen = activeFeeds.has(cid);
    btn.disabled = isRecording || !isOpen;
  });
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
      let detail = cam.error;
      if (!detail) {
        detail = cam.status === "idle" ? (ready?.reason ?? "no frame yet") : `${cam.frames} frames`;
      } else if (
        detail.includes("ended unexpectedly") ||
        detail.includes("Error during demuxing") ||
        detail.includes("Camera stream ended")
      ) {
        detail = "Camera disconnected";
      }
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
  document.addEventListener("click", (e) => {
    const target = e.target as HTMLElement | null;
    if (!target) return;
    if (target.matches(".btn-open-feed")) {
      const cid = (target as HTMLButtonElement).dataset.cameraId;
      if (cid && state && state.ride === "idle") startCameraStream(cid);
    } else if (target.matches(".btn-close-feed")) {
      const cid = (target as HTMLButtonElement).dataset.cameraId;
      if (cid) stopCameraStream(cid);
    }
  });
  // If a stream fails to load (camera busy, 409, 404...), reset the buttons so Open works again.
  // "error" does not bubble, so listen in the capture phase.
  document.addEventListener(
    "error",
    (e) => {
      const t = e.target;
      if (t instanceof HTMLImageElement && t.dataset.camera && activeFeeds.has(t.dataset.camera)) {
        const cid = t.dataset.camera;
        stopCameraStream(cid);
        addNotice({ level: "warning", text: `Could not open preview for ${cid}` });
      }
    },
    true,
  );
  socket.connect();
}

wire();