// Usage: node scripts/chart_from_label.mjs <label.bmp> [--write]
// Rebuilds the built-in chart (chart.json) from one photo of the bottle's
// label lying flat, in open shade, square to the camera. The chart is found
// in the picture and every swatch is measured against the label's own white.
// Without --write it only prints what it measured.
import fs from 'node:fs';
import { readBmp } from './bmp.mjs';
import { findChartGrid, measureChartGrid, chartCounts, chartProblems } from '../public/strip.js';
import { labToCss } from '../public/color.js';

const [file, flag] = process.argv.slice(2);
const target = new URL('../chart.json', import.meta.url);
const chart = JSON.parse(fs.readFileSync(target));
const img = readBmp(file);
const marks = findChartGrid(img, chart);
if (!marks) { console.error('No chart found in this picture.'); process.exit(1); }
const grid = measureChartGrid(img, marks, chartCounts(chart)), problems = chartProblems(grid);
chart.padOrder.forEach((key, r) => {
  console.log(key.padEnd(4), grid[r].map((cell, k) => `${chart.params[key].swatches[k].value}: ${cell.lab.join(',')} (white ${cell.whiteLevel})`).join('   '));
  chart.params[key].swatches = chart.params[key].swatches.map((swatch, k) => ({ value: swatch.value, lab: grid[r][k].lab, css: labToCss(grid[r][k].lab) }));
});
if (problems.length) { console.error('Not usable:', JSON.stringify(problems)); process.exit(1); }
if (flag === '--write') {
  chart.basis = 'the bottle\'s label photographed flat in open shade, every swatch measured against the label\'s own white';
  fs.writeFileSync(target, JSON.stringify(chart, null, 2) + '\n');
  console.log('chart.json written');
}
