import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { linearToLab, labToLinear, whiteRelativeLab, srgbToLinear } from '../public/color.js';
import { matchColour, readStrip, readStripEitherWay, findStrip, roundTo } from '../public/strip.js';
import { readBmp } from '../scripts/bmp.mjs';

const chart = JSON.parse(fs.readFileSync(new URL('../chart.json', import.meta.url)));
const encode = v => Math.round(255 * (v <= 0.0031308 ? 12.92 * v : 1.055 * Math.max(0, v) ** (1 / 2.4) - 0.055));

test('white is L 100 with no colour, and Lab round-trips', () => {
  const lab = linearToLab([1, 1, 1]);
  assert.ok(Math.abs(lab[0] - 100) < 0.01 && Math.abs(lab[1]) < 0.01 && Math.abs(lab[2]) < 0.01);
  const back = labToLinear(linearToLab([0.2, 0.5, 0.7]));
  [0.2, 0.5, 0.7].forEach((v, i) => assert.ok(Math.abs(back[i] - v) < 1e-6));
});

test('a patch is measured relative to its own white, whatever the light', () => {
  const patch = [0.3, 0.2, 0.1], white = [0.9, 0.8, 0.7];
  const dim = whiteRelativeLab(patch.map(v => v * 0.4), white.map(v => v * 0.4));
  whiteRelativeLab(patch, white).forEach((v, i) => assert.ok(Math.abs(v - dim[i]) < 1e-9));
});

test('the chart is complete and ordered', () => {
  assert.deepEqual(chart.padOrder, ['fc', 'ta', 'ph', 'cya']);
  for (const key of chart.padOrder) {
    const values = chart.params[key].swatches.map(s => s.value);
    assert.deepEqual(values, [...values].sort((a, b) => a - b));
    assert.ok(values.length >= 5);
  }
});

test('a swatch colour reads as its own value, a blend reads in between', () => {
  for (const key of chart.padOrder) {
    const sw = chart.params[key].swatches;
    for (const s of sw) assert.ok(Math.abs(matchColour(s.lab, sw).value - s.value) < 1e-6, `${key} ${s.value}`);
  }
  const [a, b] = chart.params.ph.swatches.slice(1, 3);
  const mid = matchColour(a.lab.map((v, i) => (v + b.lab[i]) / 2), chart.params.ph.swatches);
  assert.ok(Math.abs(mid.value - (a.value + b.value) / 2) < 0.05);
  assert.ok(mid.low <= mid.value && mid.high >= mid.value);
});

test('rounding follows each pad', () => {
  assert.equal(roundTo(7.44, 0.1), 7.4); assert.equal(roundTo(87, 10), 90); assert.equal(roundTo(1.3, 0.5), 1.5);
});

// On the bottle's label the pH 7.2 and 7.6 swatches are almost the same colour, so a pad painted in one
// of them (and rounded to 8 bits) can land a step along the path between them. Every other pad is exact.
const same = (key, got, want) => Math.abs(got - want) <= (key === 'ph' ? 0.15 : 0);

// A synthetic strip on white paper, with pads painted in exact chart colours.
function synthetic(values, { vertical = true, flip = false, paper = 0.62 } = {}) {
  const width = vertical ? 300 : 700, height = vertical ? 700 : 300;
  const data = new Uint8ClampedArray(width * height * 4);
  const put = (x, y, lin) => { const i = (y * width + x) * 4; data[i] = encode(lin[0]); data[i + 1] = encode(lin[1]); data[i + 2] = encode(lin[2]); data[i + 3] = 255; };
  const plastic = [paper * 0.97, paper * 0.98, paper];
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) put(x, y, [paper, paper, paper * 0.98]);
  const pitch = 120, pad = 48, start = 110, centres = [];
  const keys = flip ? [...chart.padOrder].reverse() : chart.padOrder;
  for (let along = start - 30; along < start + pitch * 3 + 260; along++) for (let across = 126; across < 174; across++)
    if (along < (vertical ? height : width)) vertical ? put(across, along, plastic) : put(along, across, plastic);
  keys.forEach((key, i) => {
    const sw = chart.params[key].swatches.find(s => s.value === values[key]);
    const lin = labToLinear(sw.lab).map((v, ch) => Math.max(0, v) * plastic[ch]);
    const c = start + i * pitch;
    for (let along = c - pad / 2; along < c + pad / 2; along++) for (let across = 126; across < 174; across++) vertical ? put(across, along, lin) : put(along, across, lin);
    centres.push(vertical ? { x: 150, y: c } : { x: c, y: 150 });
  });
  return { img: { width, height, data }, centres, keys };
}

test('a strip on white paper is found and read without help', () => {
  const values = { fc: 3, ta: 120, ph: 7.6, cya: 50 };
  for (const options of [{}, { flip: true }, { vertical: false }, { vertical: false, flip: true, paper: 0.4 }]) {
    const { img } = synthetic(values, options);
    const found = findStrip(img, chart);
    assert.ok(found, 'strip found ' + JSON.stringify(options));
    const read = readStrip(img, found.first, found.last, chart);
    for (const key of chart.padOrder) assert.ok(same(key, read.readings[key].value, values[key]), `${key} ${JSON.stringify(options)}: ${read.readings[key].value}`);
    assert.deepEqual(read.warnings, []);
  }
});

test('a white zero-chlorine pad does not stop the strip being found', () => {
  const values = { fc: 0, ta: 80, ph: 7.2, cya: 0 };
  const { img } = synthetic(values);
  const found = findStrip(img, chart);
  assert.ok(found);
  const read = readStrip(img, found.first, found.last, chart);
  for (const key of chart.padOrder) assert.ok(same(key, read.readings[key].value, values[key]), `${key}: ${read.readings[key].value}`);
});

test('a blank sheet has no strip', () => {
  const data = new Uint8ClampedArray(200 * 200 * 4).fill(200);
  assert.equal(findStrip({ width: 200, height: 200, data }, chart), null);
});

test('blown-out whites are reported', () => {
  const { img, centres } = synthetic({ fc: 1, ta: 80, ph: 7.2, cya: 50 }, { paper: 1 });
  const read = readStrip(img, centres[0], centres[3], chart);
  assert.ok(read.warnings.some(w => w.code === 'bright'));
});

test('the dry strip photographed beside the bottle reads bottom-of-scale either way up', () => {
  const img = readBmp(new URL('./fixtures/dry-strip.bmp', import.meta.url).pathname.replace(/%20/g, ' '));
  const tip = { x: 64, y: 68 }, handle = { x: 54, y: 490 };
  for (const [a, b] of [[tip, handle], [handle, tip]]) {
    const read = readStripEitherWay(img, a, b, chart);
    assert.ok(read.readings.fc.value <= 1, 'chlorine ' + read.readings.fc.value);
    assert.ok(read.readings.ta.value <= 60, 'alkalinity ' + read.readings.ta.value);
    assert.ok(read.readings.ph.value <= 6.8, 'pH ' + read.readings.ph.value);
    assert.ok(read.readings.cya.value <= 30, 'stabiliser ' + read.readings.cya.value);
    assert.ok(Math.abs(read.layout.first.y - tip.y) < 1, 'chlorine pad identified as the tip');
    assert.ok(read.warnings.some(w => w.code === 'bright'), 'over-exposure flagged');
  }
});

test('an empty white tray is not mistaken for a strip, a strip is', async () => {
  const { stripPresent } = await import('../public/strip.js');
  const { img, centres } = synthetic({ fc: 1, ta: 80, ph: 7.2, cya: 50 });
  assert.equal(stripPresent(readStrip(img, centres[0], centres[3], chart)), true);
  const blank = { width: 300, height: 700, data: new Uint8ClampedArray(300 * 700 * 4).fill(190) };
  assert.equal(stripPresent(readStrip(blank, centres[0], centres[3], chart)), false);
});

// The bottle's label stuck flat on the back of the tray, photographed in shade
// on a stone floor (cropped and halved). Swatch centres read off the picture.
const label = () => readBmp(new URL('./fixtures/flat-label.bmp', import.meta.url).pathname.replace(/%20/g, ' '));
const LABEL_MARKS = { a: { x: 132, y: 135 }, b: { x: 354, y: 134 }, c: { x: 132, y: 364 } };
const close = (p, q, reach = 5) => Math.hypot(p.x - q.x, p.y - q.y) < reach;

// The same picture turned clockwise by a number of quarter turns, and where a point ends up.
function turned(img, quarters) {
  let { width: w, height: h, data } = img, map = p => p;
  for (let q = 0; q < quarters; q++) {
    const out = new Uint8ClampedArray(data.length), H = h, before = map;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const s = (y * w + x) * 4, d = (x * h + (h - 1 - y)) * 4;
      out[d] = data[s]; out[d + 1] = data[s + 1]; out[d + 2] = data[s + 2]; out[d + 3] = 255;
    }
    data = out; [w, h] = [h, w];
    map = p => { const m = before(p); return { x: H - 1 - m.y, y: m.x }; };
  }
  return { img: { width: w, height: h, data }, map };
}

test('the whole chart is found on the flat label, whichever way up it lies', async () => {
  const { findChartGrid } = await import('../public/strip.js');
  for (const quarters of [0, 1, 2, 3]) {
    const { img, map } = turned(label(), quarters);
    const marks = findChartGrid(img, chart);
    assert.ok(marks, `found after ${quarters} quarter turns`);
    for (const name of ['a', 'b', 'c']) assert.ok(close(marks[name], map(LABEL_MARKS[name])), `${name} after ${quarters} quarter turns: ${JSON.stringify(marks[name])}`);
  }
  const blank = { width: 300, height: 300, data: new Uint8ClampedArray(300 * 300 * 4).fill(200) };
  assert.equal(findChartGrid(blank, chart), null);
});

test('every swatch of the chart is measured, and each row runs the way the label does', async () => {
  const { measureChartGrid, chartCounts, chartProblems } = await import('../public/strip.js');
  assert.deepEqual(chartCounts(chart), [6, 6, 6, 5]);
  const grid = measureChartGrid(label(), LABEL_MARKS, chartCounts(chart));
  assert.deepEqual(grid.map(row => row.length), [6, 6, 6, 5]);
  assert.deepEqual(chartProblems(grid), []);
  const [fc, ta, ph, cya] = grid.map(row => row.map(cell => cell.lab));
  assert.ok(fc[0][0] > 90 && fc[5][0] < 60 && fc[5][1] > 30 && fc[5][2] < -10, 'chlorine runs from near-white to purple');
  assert.ok(ta[0][2] > 50 && ta[5][2] < -5 && ta[3][1] < -15, 'alkalinity runs from yellow through green to teal');
  assert.ok(ph[0][2] > 55 && ph[5][1] > ph[0][1] + 15, 'pH runs from orange to red');
  assert.ok(cya[0][2] > 45 && cya[4][2] < -5 && cya[4][1] > 45, 'stabiliser runs from orange to purple');
  // The built-in chart was measured from this label, so it agrees with the picture.
  chart.padOrder.forEach((key, r) => grid[r].forEach((cell, k) => {
    const want = chart.params[key].swatches[k].lab;
    assert.ok(Math.hypot((cell.lab[0] - want[0]) * 0.5, cell.lab[1] - want[1], cell.lab[2] - want[2]) < 5, `${key} ${chart.params[key].swatches[k].value}: ${cell.lab} vs ${want}`);
  }));
});

test('a swatch burnt out by the torch, and a label close to blown out, are reported', async () => {
  const { measureChartGrid, chartCounts, chartProblems, chartGridLayout } = await import('../public/strip.js');
  const img = label(), counts = chartCounts(chart), at = chartGridLayout(LABEL_MARKS, counts).cells[2][3];
  for (let y = Math.round(at.y) - 3; y <= Math.round(at.y) + 3; y++) for (let x = Math.round(at.x) - 3; x <= Math.round(at.x) + 3; x++) img.data.fill(255, (y * img.width + x) * 4, (y * img.width + x) * 4 + 4);
  assert.deepEqual(chartProblems(measureChartGrid(img, LABEL_MARKS, counts)).filter(q => q.code === 'glare'), [{ code: 'glare', row: 2, column: 3 }]);
  // Rings dragged off the swatches land on white label: that is caught when the chart is given.
  const off = { a: { x: LABEL_MARKS.a.x, y: LABEL_MARKS.a.y + 38 }, b: { x: LABEL_MARKS.b.x, y: LABEL_MARKS.b.y + 38 }, c: { x: LABEL_MARKS.c.x, y: LABEL_MARKS.c.y + 38 } };
  // (In the box the label's white is well short of burnt out; this shade photo is dimmed to match.)
  const dim = label();
  for (let i = 0; i < dim.data.length; i += 4) for (let ch = 0; ch < 3; ch++) dim.data[i + ch] = Math.round(dim.data[i + ch] * 0.9);
  const missed = chartProblems(measureChartGrid(dim, off, counts), chart);
  assert.ok(missed.filter(q => q.code === 'missed').length >= 17 && !missed.some(q => q.code === 'glare'), JSON.stringify(missed));
  assert.deepEqual(chartProblems(measureChartGrid(dim, LABEL_MARKS, counts), chart), []);
  assert.deepEqual(chartProblems(measureChartGrid(label(), LABEL_MARKS, counts), chart), []);
  const bright = label();
  for (let i = 0; i < bright.data.length; i += 4) for (let ch = 0; ch < 3; ch++) bright.data[i + ch] = Math.min(255, bright.data[i + ch] + 10);
  assert.ok(chartProblems(measureChartGrid(bright, LABEL_MARKS, counts)).some(q => q.code === 'bright'));
});
