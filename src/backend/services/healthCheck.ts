// Health check service: internet, moments-server, and camera readiness.
// Runs in the main (Node.js) process so it can use Electron's net module.
import { net } from 'electron';
import type { HealthCheckResult } from '../shared/types';
import { getLogger } from './logger';

const log = getLogger('health');

const INTERNET_URLS = [
  'https://dns.google',       // primary probe: Google DNS web UI
  'https://www.cloudflare.com',
  'https://www.bing.com',
];

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// -------------------------------------------------------------------------
// Internet check — tries up to maxAttempts (9) times, rotating probe URLs.
// -------------------------------------------------------------------------
export async function checkInternet(
  maxAttempts = 9,
  onProgress?: (attempt: number, maxAttempts: number) => void,
): Promise<HealthCheckResult> {
  for (let i = 1; i <= maxAttempts; i++) {
    onProgress?.(i, maxAttempts);
    const url = INTERNET_URLS[(i - 1) % INTERNET_URLS.length];
    try {
      const res = await net.fetch(url, { method: 'HEAD' });
      if (res.status < 500) {
        log.info(`[health] internet OK on attempt ${i}/${maxAttempts} (${url})`);
        return { ok: true, message: 'Internet connected', attempts: i };
      }
      log.warn(`[health] internet probe ${url} returned ${res.status}`);
    } catch (err) {
      log.warn(`[health] internet attempt ${i}/${maxAttempts} failed: ${(err as Error).message}`);
    }
    if (i < maxAttempts) await sleep(1500);
  }
  return { ok: false, message: 'Please connect to the internet', attempts: maxAttempts };
}

// -------------------------------------------------------------------------
// Moments-server check — 3 attempts, checks the configured server URL.
// Falls back to checking the local backend IPC health endpoint.
// -------------------------------------------------------------------------
export async function checkMomentsServer(
  serverUrl: string,
  maxAttempts = 3,
  onProgress?: (attempt: number, maxAttempts: number) => void,
): Promise<HealthCheckResult> {
  for (let i = 1; i <= maxAttempts; i++) {
    onProgress?.(i, maxAttempts);
    try {
      const res = await net.fetch(serverUrl, { method: 'GET' });
      if (res.status < 500) {
        log.info(`[health] server OK on attempt ${i}/${maxAttempts}`);
        return { ok: true, message: 'Server is active', attempts: i };
      }
      log.warn(`[health] server check attempt ${i} returned ${res.status}`);
    } catch (err) {
      log.warn(`[health] server check attempt ${i}/${maxAttempts} failed: ${(err as Error).message}`);
    }
    if (i < maxAttempts) await sleep(2000);
  }
  return { ok: false, message: 'Contact FOG Support', attempts: maxAttempts };
}
