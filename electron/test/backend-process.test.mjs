// Run with: npm test   (compiles electron/src first)
import assert from "node:assert/strict";
import http from "node:http";
import { test } from "node:test";
import {
  BackendProcess,
  isRecording,
  resolveBackendCommand,
  waitForHealth,
} from "../dist/backend-process.js";

function server(handler) {
  return new Promise((resolve) => {
    const s = http.createServer(handler);
    s.listen(0, "127.0.0.1", () => resolve({ s, port: s.address().port }));
  });
}

test("resolveBackendCommand: dev uses python -m, packaged uses bundled exe", () => {
  const dev = resolveBackendCommand({ isPackaged: false, resourcesPath: "R", repoRoot: "/repo", env: {} });
  assert.deepEqual([dev.command, dev.args, dev.cwd], ["python", ["-m", "backend.app.main"], "/repo"]);
  const custom = resolveBackendCommand({ isPackaged: false, resourcesPath: "R", repoRoot: "/r", env: { BUMPER_PYTHON: "py3" } });
  assert.equal(custom.command, "py3");
  const prod = resolveBackendCommand({ isPackaged: true, resourcesPath: "/res", repoRoot: "/repo", env: {} });
  assert.match(prod.command.replaceAll("\\", "/"), /\/res\/backend\/bumper-backend\.exe$/);
  assert.deepEqual(prod.args, []);
});

test("waitForHealth resolves once the backend reports ok", async () => {
  let calls = 0;
  const { s, port } = await server((req, res) => {
    calls += 1;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ status: calls < 3 ? "starting" : "ok" }));
  });
  await waitForHealth(port, { intervalMs: 10, timeoutMs: 5000 });
  assert.ok(calls >= 3);
  s.closeAllConnections();
  s.close();
});

test("waitForHealth times out when nothing listens", async () => {
  await assert.rejects(waitForHealth(1, { intervalMs: 10, timeoutMs: 100 }), /did not become ready/);
});

test("waitForHealth gives up immediately when the backend process died", async () => {
  await assert.rejects(waitForHealth(1, { intervalMs: 10, timeoutMs: 5000, aborted: () => true }), /exited/);
});

test("isRecording reads the ride state and treats errors as not recording", async () => {
  const { s, port } = await server((req, res) => res.end(JSON.stringify({ status: "ok", ride: "recording" })));
  assert.equal(await isRecording(port), true);
  s.close();
  assert.equal(await isRecording(1), false);
});

test("stop() asks the backend to quit and waits for it to exit (no kill)", async () => {
  let quitCalled = false;
  const { s, port } = await server((req, res) => {
    if (req.method === "POST" && req.url === "/api/app/quit") quitCalled = true;
    res.end("{}");
  });
  // A child that exits by itself shortly after being asked.
  const proc = new BackendProcess({ command: process.execPath, args: ["-e", "setTimeout(()=>{}, 600)"], cwd: process.cwd() }, port);
  proc.start();
  const t0 = Date.now();
  await proc.stop(5000);
  assert.ok(quitCalled);
  assert.ok(proc.hasExited);
  assert.ok(Date.now() - t0 < 4000);
  s.closeAllConnections();
  s.close();
});

test("stop() kills a backend that ignores the quit request after the grace period", async () => {
  const { s, port } = await server((req, res) => res.end("{}"));
  try {
    const proc = new BackendProcess({ command: process.execPath, args: ["-e", "setInterval(()=>{}, 1000)"], cwd: process.cwd() }, port);
    proc.start();
    await proc.stop(300);
    assert.ok(proc.hasExited);
  } finally {
    s.closeAllConnections();
    s.close();
  }
});

test("a missing backend executable is reported as an exit with an error", async () => {
  const proc = new BackendProcess({ command: "definitely-not-a-real-binary", args: [], cwd: process.cwd() }, 1);
  const exited = new Promise((resolve) => proc.once("exit", (code, err) => resolve({ code, err })));
  proc.start();
  const r = await exited;
  assert.equal(r.code, null);
  assert.ok(r.err);
});
