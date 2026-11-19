// The ONLY thing the web UI may ask the desktop shell to do is quit.
// All application logic lives in the Python backend.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("desktop", {
  quit: (): Promise<void> => ipcRenderer.invoke("desktop:quit"),
});
