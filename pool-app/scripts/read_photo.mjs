// Usage: node scripts/read_photo.mjs <photo.bmp> <x1> <y1> <x2> <y2>
// Reads a strip given the centres of its first and last pads.
import fs from 'node:fs';
import { readBmp } from './bmp.mjs';
import { readStripEitherWay } from '../public/strip.js';
const [file, x1, y1, x2, y2] = process.argv.slice(2);
const chart = JSON.parse(fs.readFileSync(new URL('../chart.json', import.meta.url)));
const result = readStripEitherWay(readBmp(file), { x: +x1, y: +y1 }, { x: +x2, y: +y2 }, chart);
for (const [key, r] of Object.entries(result.readings))
  console.log(key.padEnd(4), String(r.value).padStart(5), `(${r.low}-${r.high})`.padEnd(12), r.confidence.padEnd(5), 'd=' + r.distance, 'lab', r.lab.join(','), 'white', r.whiteLevel, `@${Math.round(r.x)},${Math.round(r.y)}`);
for (const w of result.warnings) console.log('! ' + w.text);
