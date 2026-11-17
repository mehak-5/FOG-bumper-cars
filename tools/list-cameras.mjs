// Prints webcam names (Windows DirectShow) to copy into config/venue.yaml.
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
let ffmpeg = process.env.FFMPEG_PATH ?? 'ffmpeg';
try {
  const staticFfmpeg = require('ffmpeg-static');
  if (staticFfmpeg) ffmpeg = process.env.FFMPEG_PATH ?? staticFfmpeg;
} catch {}
const p = spawn(ffmpeg, ['-hide_banner', '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy']);
let out = '';
p.stderr.on('data', (d) => (out += d));
p.on('error', (e) => {
  console.error(`Cannot run ffmpeg ('${ffmpeg}'): ${e.message}`);
  process.exit(1);
});
p.on('close', () => {
  const names = [...new Set([...out.matchAll(/"([^"]+)" \(video\)/g)].map((m) => m[1]))];
  if (!names.length) console.log('No webcams found.');
  names.forEach((n, i) => console.log(`#${i}  "${n}"`));
  console.log('\nUse the index (source: 0) or the exact name in config/venue.yaml.');
});
