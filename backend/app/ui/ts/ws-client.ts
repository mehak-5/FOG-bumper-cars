// WebSocket client with auto-reconnect. Message types come from messages.generated.ts, which is
// generated from the Pydantic models, so the browser and the backend can never drift apart.
import type { ClientMessageType, ClientPayloads, ServerMessage } from "./messages.generated.js";

export type SocketStatus = "connecting" | "open" | "closed";

const MIN_DELAY_MS = 500;
const MAX_DELAY_MS = 5000;
const PING_EVERY_MS = 15000;

export function socketUrl(): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/ws`;
}

export class OperatorSocket {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private retryTimer: number | undefined;
  private pingTimer: number | undefined;
  private closedByUser = false;

  constructor(
    private readonly onMessage: (msg: ServerMessage) => void,
    private readonly onStatus: (status: SocketStatus) => void,
    private readonly onOpen: () => void = () => {},
    private readonly url: string = socketUrl(),
  ) {}

  connect(): void {
    this.closedByUser = false;
    this.onStatus("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.addEventListener("open", () => {
      this.attempt = 0;
      this.onStatus("open");
      this.pingTimer = window.setInterval(() => this.send("ping"), PING_EVERY_MS);
      this.onOpen();
    });
    ws.addEventListener("message", (ev: MessageEvent<unknown>) => {
      if (typeof ev.data !== "string") return;
      try {
        this.onMessage(JSON.parse(ev.data) as ServerMessage);
      } catch (err) {
        console.error("Bad message from server", err);
      }
    });
    ws.addEventListener("close", () => this.handleClose(ws));
    ws.addEventListener("error", () => ws.close());
  }

  send<K extends ClientMessageType>(type: K, payload?: ClientPayloads[K]): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ type, id: crypto.randomUUID(), ts: new Date().toISOString(), payload: payload ?? {} }));
    return true;
  }

  close(): void {
    this.closedByUser = true;
    window.clearTimeout(this.retryTimer);
    this.ws?.close();
  }

  private handleClose(ws: WebSocket): void {
    if (this.ws !== ws) return;
    window.clearInterval(this.pingTimer);
    this.onStatus("closed");
    if (this.closedByUser) return;
    const delay = Math.min(MIN_DELAY_MS * 2 ** this.attempt, MAX_DELAY_MS);
    this.attempt += 1;
    this.retryTimer = window.setTimeout(() => this.connect(), delay);
  }
}

export function setConnectionBadge(status: SocketStatus): void {
  const el = document.getElementById("conn");
  if (!el) return;
  el.dataset.status = status;
  el.textContent = status === "open" ? "Live" : status === "connecting" ? "Connecting…" : "Offline - retrying";
}
