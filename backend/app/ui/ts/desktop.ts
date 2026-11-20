// Desktop-only helper: the Electron preload exposes exactly one function, desktop.quit().
// In a plain browser window.desktop is undefined and the Quit button stays hidden.
declare global {
  interface Window {
    desktop?: { quit(): Promise<void> };
  }
}

const button = document.getElementById("btn-quit") as HTMLButtonElement | null;
const desktop = window.desktop;

if (button && desktop) {
  button.hidden = false;
  button.addEventListener("click", () => {
    if (confirm("Quit the application? An active ride will be stopped and saved.")) {
      void desktop.quit();
    }
  });
}

export {};
