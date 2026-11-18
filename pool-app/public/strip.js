// Reads a test strip from a photo. Pure functions over an ImageData-like
// object ({width, height, data: RGBA bytes in sRGB}) so the same code runs in
// the browser and in the Node tests.
import { linearLut, trimmedMean, whiteOf, whiteRelativeLab, distance, L_WEIGHT } from './color.js';

// Strip geometry as fractions of the pad pitch (centre-to-centre distance).
const PAD_HALF = 0.11;     // sampled square inside a pad (pads are ~0.4 pitch wide)
const WHITE_HALF = 0.07;   // sampled square of bare plastic between pads
const SEARCH = 0.25;       // how far a pad may sit from its nominal position
const CLIP_LEVEL = 243;    // sRGB level above which white is considered blown out

function sample(img, cx, cy, half) {
  const out = [], w = img.width, h = img.height, d = img.data;
  const x0 = Math.max(0, Math.round(cx - half)), x1 = Math.min(w - 1, Math.round(cx + half));
  const y0 = Math.max(0, Math.round(cy - half)), y1 = Math.min(h - 1, Math.round(cy + half));
  const stepPx = Math.max(1, Math.floor((x1 - x0) / 24));
  for (let y = y0; y <= y1; y += stepPx) for (let x = x0; x <= x1; x += stepPx) {
    const i = (y * w + x) * 4;
    out.push([linearLut[d[i]], linearLut[d[i + 1]], linearLut[d[i + 2]]]);
  }
  return out;
}

const encode = v => Math.round(255 * (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055));

// Four nominal pad centres from the centres of the first (tip, chlorine) and
// last (stabiliser) pads.
export function layoutFromEnds(first, last, count = 4) {
  const step = { x: (last.x - first.x) / (count - 1), y: (last.y - first.y) / (count - 1) };
  const pitch = Math.hypot(step.x, step.y);
  return {
    first, last, step, pitch,
    pads: Array.from({ length: count }, (_, i) => ({ x: first.x + step.x * i, y: first.y + step.y * i })),
  };
}

// Bare-plastic white next to pad i: midway to each neighbouring pad (and the
// same distance past the last pad, where the handle continues).
function whiteNear(img, layout, i, at) {
  const { step, pitch, pads } = layout, samples = [];
  const sides = i === 0 ? [0.5] : [-0.5, 0.5];
  for (const side of sides) samples.push(...sample(img, at.x + step.x * side, at.y + step.y * side, pitch * WHITE_HALF));
  return whiteOf(samples, 0.5, 0.02);
}

// Pads are not perfectly evenly spaced in a photo (perspective, a bent strip),
// so the two middle pads are nudged along the strip to where the colour is
// strongest. The end pads are where the person (or the detector) put them.
function refine(img, layout, i) {
  const { step, pitch, pads } = layout, base = pads[i];
  if (i === 0 || i === pads.length - 1) return base;
  const white = whiteNear(img, layout, i, base);
  let best = { offset: 0, strength: -Infinity };
  for (let o = -SEARCH; o <= SEARCH + 1e-9; o += 0.05) {
    const p = { x: base.x + step.x * o, y: base.y + step.y * o };
    const lab = whiteRelativeLab(trimmedMean(sample(img, p.x, p.y, pitch * PAD_HALF)), white);
    const strength = Math.hypot(lab[1], lab[2]) - Math.abs(o) * 10;
    if (strength > best.strength) best = { offset: o, strength };
  }
  if (best.strength < 12) return base;
  return { x: base.x + step.x * best.offset, y: base.y + step.y * best.offset };
}

// Measure every pad: white-relative Lab plus the quality facts the UI needs.
export function measurePads(img, layout, whiteAdjust = [1, 1, 1]) {
  return layout.pads.map((nominal, i) => {
    const at = refine(img, layout, i);
    const half = layout.pitch * PAD_HALF;
    const pixels = sample(img, at.x, at.y, half);
    const patch = trimmedMean(pixels);
    const rawWhite = whiteNear(img, layout, i, at);
    const white = rawWhite.map((v, ch) => v * whiteAdjust[ch]);
    // Unevenness inside the pad: glare, a water bead, or a badly placed marker.
    const quarters = [[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5], [0.5, 0.5]].map(([dx, dy]) =>
      whiteRelativeLab(trimmedMean(sample(img, at.x + dx * half, at.y + dy * half, half / 2)), white));
    let uneven = 0;
    for (const q of quarters) for (const r of quarters) uneven = Math.max(uneven, distance(q, r));
    return {
      x: at.x, y: at.y, half,
      lab: whiteRelativeLab(patch, white),
      whiteLevel: Math.max(...rawWhite.map(encode)),
      whiteTint: Math.hypot(...whiteRelativeLab(rawWhite, [Math.max(...rawWhite), Math.max(...rawWhite), Math.max(...rawWhite)]).slice(1)),
      uneven,
    };
  });
}

// Where a colour falls on a parameter's chart. The chart swatches form a path
// through colour space; the reading is the nearest point on that path, and
// the plausible range is every part of the path that is nearly as close.
export function matchColour(lab, swatches, tolerance = 4) {
  const sc = p => [p[0] * L_WEIGHT, p[1], p[2]];
  const q = sc(lab);
  let best = null;
  const near = [];
  for (let i = 0; i < swatches.length - 1; i++) {
    const a = sc(swatches[i].lab), b = sc(swatches[i + 1].lab);
    const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const len2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2;
    for (let s = 0; s <= 20; s++) {
      const t = s / 20;
      const p = [a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t];
      const d = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]);
      const value = swatches[i].value + (swatches[i + 1].value - swatches[i].value) * t;
      near.push({ d, value });
      if (!best || d < best.d) best = { d, value, segment: i, t };
    }
    // Exact projection for the best point on this segment.
    const t = Math.min(1, Math.max(0, ((q[0] - a[0]) * ab[0] + (q[1] - a[1]) * ab[1] + (q[2] - a[2]) * ab[2]) / (len2 || 1)));
    const p = [a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t];
    const d = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]);
    if (d < best.d) best = { d, value: swatches[i].value + (swatches[i + 1].value - swatches[i].value) * t, segment: i, t };
  }
  const close = near.filter(n => n.d <= best.d + tolerance).map(n => n.value);
  const lowest = swatches[0].value, highest = swatches[swatches.length - 1].value;
  return {
    value: best.value,
    low: Math.min(best.value, ...close), high: Math.max(best.value, ...close),
    distance: best.d,
    atMin: best.value <= lowest + 1e-9, atMax: best.value >= highest - 1e-9,
  };
}

export function roundTo(value, step) {
  const r = Math.round(value / step) * step;
  return +r.toFixed(step < 1 ? 1 : 0);
}

// Full read of a strip whose end pads are known.
export function readStrip(img, first, last, chart) {
  const layout = layoutFromEnds(first, last, chart.padOrder.length);
  const pads = measurePads(img, layout, chart.whiteAdjust);
  const warnings = [];
  const readings = {};
  chart.padOrder.forEach((key, i) => {
    const param = chart.params[key], pad = pads[i];
    const m = matchColour(pad.lab, param.swatches);
    const confidence = m.distance < 8 ? 'good' : m.distance < 16 ? 'fair' : 'poor';
    readings[key] = {
      value: roundTo(m.value, param.step), low: roundTo(m.low, param.step), high: roundTo(m.high, param.step),
      exact: m.value, distance: +m.distance.toFixed(1), confidence, atMin: m.atMin, atMax: m.atMax,
      lab: pad.lab.map(v => +v.toFixed(1)), x: pad.x, y: pad.y, half: pad.half, whiteLevel: pad.whiteLevel,
    };
  });
  const levels = pads.map(p => p.whiteLevel);
  if (Math.max(...levels) >= CLIP_LEVEL) warnings.push({ code: 'bright', text: 'The white of the strip is blown out, so colours read too pale. Lay the strip on white paper in open shade and retake.' });
  if (Math.min(...levels) < 110) warnings.push({ code: 'dark', text: 'The photo is quite dark. Move to brighter shade and retake.' });
  if (pads.some(p => p.uneven > 14)) warnings.push({ code: 'uneven', text: 'A pad looks uneven (glare, a water drop, or the markers are off a pad). Check the markers or retake.' });
  if (Object.values(readings).some(r => r.confidence === 'poor')) warnings.push({ code: 'offchart', text: 'A pad colour does not sit on the bottle chart. Check the markers are on the pads, or retake in open shade.' });
  return { layout: { first: layout.first, last: layout.last, pitch: layout.pitch }, readings, warnings };
}

// Try both directions for a pair of end points and keep the one whose pad
// colours fit the chart best (the alkalinity pad is the only green/yellow one).
export function readStripEitherWay(img, a, b, chart) {
  const one = readStrip(img, a, b, chart), two = readStrip(img, b, a, chart);
  const score = r => Object.values(r.readings).reduce((s, v) => s + v.distance, 0);
  return score(one) <= score(two) ? one : two;
}

// ---------------------------------------------------------------------------
// Automatic detection, for a strip lying on plain white paper: the pads are
// then the only coloured things in the picture. Returns {first, last} pad
// centres in image pixels, or null when no convincing strip is found (the
// person then drags the markers instead).
// Roughly square blobs of colour in a shrunken copy of the picture (about
// `target` pixels on its long side). Positions and sizes are in shrunken
// pixels; multiply by `scale` (after adding 0.5) for picture pixels.
function colouredBlobs(img, target = 360) {
  const scale = Math.max(1, Math.ceil(Math.max(img.width, img.height) / target));
  const w = Math.floor(img.width / scale), h = Math.floor(img.height / scale);
  const lin = new Float32Array(w * h * 3);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let r = 0, g = 0, b = 0, n = 0;
    for (let dy = 0; dy < scale; dy += Math.max(1, scale >> 1)) for (let dx = 0; dx < scale; dx += Math.max(1, scale >> 1)) {
      const i = ((y * scale + dy) * img.width + x * scale + dx) * 4;
      r += linearLut[img.data[i]]; g += linearLut[img.data[i + 1]]; b += linearLut[img.data[i + 2]]; n++;
    }
    const o = (y * w + x) * 3; lin[o] = r / n; lin[o + 1] = g / n; lin[o + 2] = b / n;
  }
  const all = [];
  for (let i = 0; i < w * h; i += 7) all.push([lin[i * 3], lin[i * 3 + 1], lin[i * 3 + 2]]);
  const white = whiteOf(all, 0.3, 0.02);
  const mask = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const lab = whiteRelativeLab([lin[i * 3], lin[i * 3 + 1], lin[i * 3 + 2]], white);
    if (Math.hypot(lab[1], lab[2]) > 16 && lab[0] > 22) mask[i] = 1;
  }
  // Connected blobs of coloured pixels.
  const blobs = [], stack = [];
  for (let start = 0; start < w * h; start++) {
    if (mask[start] !== 1) continue;
    let n = 0, sx = 0, sy = 0, x0 = w, x1 = 0, y0 = h, y1 = 0;
    stack.push(start); mask[start] = 2;
    while (stack.length) {
      const i = stack.pop(), x = i % w, y = (i - x) / w;
      n++; sx += x; sy += y; x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      if (x > 0 && mask[i - 1] === 1) { mask[i - 1] = 2; stack.push(i - 1); }
      if (x < w - 1 && mask[i + 1] === 1) { mask[i + 1] = 2; stack.push(i + 1); }
      if (y > 0 && mask[i - w] === 1) { mask[i - w] = 2; stack.push(i - w); }
      if (y < h - 1 && mask[i + w] === 1) { mask[i + w] = 2; stack.push(i + w); }
    }
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
    if (n < 12 || n > w * h * 0.05) continue;
    if (Math.max(bw, bh) / Math.min(bw, bh) > 2.4 || n / (bw * bh) < 0.5) continue;
    blobs.push({ x: sx / n, y: sy / n, size: Math.sqrt(n) });
  }
  return { blobs, scale };
}

export function findStrip(img, chart) {
  const { blobs, scale } = colouredBlobs(img);
  if (blobs.length < 3 || blobs.length > 40) return null;
  // Chains of evenly spaced, similar-sized blobs.
  let bestChain = null;
  for (const a of blobs) for (const b of blobs) {
    if (a === b) continue;
    const vx = b.x - a.x, vy = b.y - a.y, pitch = Math.hypot(vx, vy);
    if (pitch < a.size * 1.5 || pitch > a.size * 4.5) continue;
    const chain = [a];
    for (let k = 1; k < 4; k++) {
      const tx = a.x + vx * k, ty = a.y + vy * k;
      const hit = blobs.find(c => Math.hypot(c.x - tx, c.y - ty) < pitch * 0.28 && c.size > a.size * 0.5 && c.size < a.size * 2);
      if (!hit) break;
      chain.push(hit);
    }
    if (chain.length >= 3 && (!bestChain || chain.length > bestChain.chain.length)) bestChain = { chain, vx, vy };
  }
  if (!bestChain) return null;
  const { chain } = bestChain, last = chain[chain.length - 1];
  const vx = (last.x - chain[0].x) / (chain.length - 1), vy = (last.y - chain[0].y) / (chain.length - 1);
  const up = p => ({ x: (p.x + 0.5) * scale, y: (p.y + 0.5) * scale });
  // With only three coloured pads the fourth (a white, zero-chlorine pad) is
  // off one end; the chart decides which end, and which way the strip points.
  const candidates = chain.length === 4
    ? [[chain[0], last]]
    : [[{ x: chain[0].x - vx, y: chain[0].y - vy }, last], [chain[0], { x: last.x + vx, y: last.y + vy }]];
  let best = null;
  for (const [p, q] of candidates) {
    const read = readStripEitherWay(img, up(p), up(q), chart);
    const score = Object.values(read.readings).reduce((s, v) => s + v.distance, 0);
    if (!best || score < best.score) best = { score, first: read.layout.first, last: read.layout.last };
  }
  return { first: best.first, last: best.last };
}

// ---------------------------------------------------------------------------
// Whole-chart calibration: the bottle's label laid flat, every swatch in one
// picture. The chart is a grid: one row per pad, the swatches of a row one
// pitch apart, the rows CHART_ROW_RATIO pitches apart (measured from the HTH
// label). Three points fix it: the centres of the first and last swatch of the
// first row, and of the first swatch of the last row.
export const CHART_ROW_RATIO = 1.72;

export const chartCounts = chart => chart.padOrder.map(key => chart.params[key].swatches.length);

export function chartGridLayout(marks, counts) {
  const { a, b, c } = marks, cols = Math.max(...counts), rows = counts.length;
  const u = { x: (b.x - a.x) / (cols - 1), y: (b.y - a.y) / (cols - 1) };
  const v = { x: (c.x - a.x) / (rows - 1), y: (c.y - a.y) / (rows - 1) };
  return {
    u, v, pitch: Math.hypot(u.x, u.y),
    cells: counts.map((count, r) => Array.from({ length: count }, (_, k) => ({ x: a.x + v.x * r + u.x * k, y: a.y + v.y * r + u.y * k }))),
  };
}

// Every swatch against the white label in the gaps either side of it, with
// the facts needed to judge the picture: how bright that white is, whether the
// swatch is even, and how much of it is burnt out by the torch's reflection.
export function measureChartGrid(img, marks, counts) {
  const { u, pitch, cells } = chartGridLayout(marks, counts);
  const half = pitch * 0.17, d = img.data, w = img.width, h = img.height;
  return cells.map(row => row.map(at => {
    const patch = trimmedMean(sample(img, at.x, at.y, half));
    const around = [];
    for (const side of [-0.5, 0.5]) around.push(...sample(img, at.x + u.x * side, at.y + u.y * side, pitch * 0.08));
    const white = whiteOf(around, 0.5, 0.02);
    const quarters = [[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5], [0.5, 0.5]].map(([dx, dy]) =>
      whiteRelativeLab(trimmedMean(sample(img, at.x + dx * half, at.y + dy * half, half / 2)), white));
    let uneven = 0;
    for (const q of quarters) for (const r of quarters) uneven = Math.max(uneven, distance(q, r));
    let burnt = 0, seen = 0;
    for (let y = Math.max(0, Math.round(at.y - half)); y <= Math.min(h - 1, Math.round(at.y + half)); y++) for (let x = Math.max(0, Math.round(at.x - half)); x <= Math.min(w - 1, Math.round(at.x + half)); x++) {
      const i = (y * w + x) * 4; seen++;
      if (Math.max(d[i], d[i + 1], d[i + 2]) >= 250) burnt++;
    }
    return { x: at.x, y: at.y, half, lab: whiteRelativeLab(patch, white).map(n => +n.toFixed(1)), whiteLevel: Math.max(...white.map(encode)), uneven, glare: seen ? burnt / seen : 0 };
  }));
}

// What is wrong with a chart measurement, if anything: [{code, row, column}].
// Given the chart, a ring that has missed its swatch is caught too: every
// swatch that is strongly coloured on the label must come out coloured.
export function chartProblems(grid, chart) {
  const out = [];
  grid.forEach((row, r) => row.forEach((cell, k) => {
    const want = chart && chart.params[chart.padOrder[r]].swatches[k].lab;
    const missed = want && Math.hypot(want[1], want[2]) > 20 && Math.hypot(cell.lab[1], cell.lab[2]) < 8;
    const code = cell.glare > 0.02 || cell.whiteLevel >= 250 ? 'glare' : missed ? 'missed' : cell.whiteLevel >= CLIP_LEVEL ? 'bright' : cell.whiteLevel < 90 ? 'dark' : cell.uneven > 14 ? 'uneven' : null;
    if (code) out.push({ code, row: r, column: k });
  }));
  return out;
}

const gridScore = (grid, chart) => grid.reduce((sum, row, r) => sum + row.reduce((s, cell, k) => s + distance(cell.lab, chart.params[chart.padOrder[r]].swatches[k].lab), 0), 0);

// Find the chart in a picture: the swatches are blobs of colour on a regular
// grid. Any way up; the colours decide which row is which. Returns the three
// marks in picture pixels, or null (the person then drags the rings).
export function findChartGrid(img, chart) {
  const counts = chartCounts(chart), rows = counts.length, cols = Math.max(...counts);
  const { blobs, scale } = colouredBlobs(img, 480);
  if (blobs.length < 12 || blobs.length > 160) return null;
  const near = (x, y, reach) => { let best = null, bd = reach; for (const c of blobs) { const dd = Math.hypot(c.x - x, c.y - y); if (dd < bd) { bd = dd; best = c; } } return best; };
  const found = [];
  for (const a of blobs) for (const b of blobs) {
    if (a === b) continue;
    const ux = b.x - a.x, uy = b.y - a.y, pitch = Math.hypot(ux, uy);
    if (pitch < a.size * 1.05 || pitch > a.size * 1.8) continue;
    if (near(a.x - ux, a.y - uy, pitch * 0.3)) continue;            // a must begin its row
    const vx = -uy * CHART_ROW_RATIO, vy = ux * CHART_ROW_RATIO;      // a label cannot be mirrored
    for (let r0 = 0; r0 < rows; r0++) {
      const hits = [];
      for (let r = 0; r < rows; r++) for (let k = 0; k < counts[r]; k++) {
        const blob = near(a.x + vx * (r - r0) + ux * k, a.y + vy * (r - r0) + uy * k, pitch * 0.3);
        if (blob) hits.push({ r, k, blob });
      }
      if (hits.length >= 12) found.push(hits);
    }
  }
  if (!found.length) return null;
  found.sort((p, q) => q.length - p.length);
  let best = null;
  const tried = new Set();
  for (const hits of found) {
    if (tried.size >= 8) break;
    // Least-squares grid through the matched blobs: x = o + u·k + v·r, likewise y.
    let n = 0, sk = 0, sr = 0, skk = 0, srr = 0, skr = 0; const sx = [0, 0, 0], sy = [0, 0, 0];
    for (const { r, k, blob } of hits) { n++; sk += k; sr += r; skk += k * k; srr += r * r; skr += k * r; sx[0] += blob.x; sx[1] += blob.x * k; sx[2] += blob.x * r; sy[0] += blob.y; sy[1] += blob.y * k; sy[2] += blob.y * r; }
    const solve = t => {
      const m = [[n, sk, sr, t[0]], [sk, skk, skr, t[1]], [sr, skr, srr, t[2]]];
      for (let i = 0; i < 3; i++) {
        let p = i; for (let j = i + 1; j < 3; j++) if (Math.abs(m[j][i]) > Math.abs(m[p][i])) p = j;
        [m[i], m[p]] = [m[p], m[i]];
        if (Math.abs(m[i][i]) < 1e-9) return null;
        for (let j = 0; j < 3; j++) if (j !== i) { const f = m[j][i] / m[i][i]; for (let q = i; q < 4; q++) m[j][q] -= f * m[i][q]; }
      }
      return [m[0][3] / m[0][0], m[1][3] / m[1][1], m[2][3] / m[2][2]];
    };
    const fx = solve(sx), fy = solve(sy);
    if (!fx || !fy) continue;
    const up = (x, y) => ({ x: (x + 0.5) * scale, y: (y + 0.5) * scale });
    const marks = { a: up(fx[0], fy[0]), b: up(fx[0] + fx[1] * (cols - 1), fy[0] + fy[1] * (cols - 1)), c: up(fx[0] + fx[2] * (rows - 1), fy[0] + fy[2] * (rows - 1)) };
    const id = [marks.a, marks.b].map(p => `${Math.round(p.x / scale / 2)},${Math.round(p.y / scale / 2)}`).join(' ');
    if (tried.has(id)) continue;
    tried.add(id);
    const score = gridScore(measureChartGrid(img, marks, counts), chart);
    if (!best || score < best.score) best = { score, marks };
  }
  return best && best.marks;
}

// Whether a strip is sitting where the rings are: the alkalinity, pH and
// stabiliser pads are never white, while an empty tray is.
export function stripPresent(read) {
  return ['ta', 'ph', 'cya'].filter(key => Math.hypot(read.readings[key].lab[1], read.readings[key].lab[2]) > 14).length >= 2;
}
