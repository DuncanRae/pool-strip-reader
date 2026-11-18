// Colour maths shared by the strip reader, the chart-derivation script and tests.
// Everything works in "white-relative" colour: a patch is divided, channel by
// channel in linear light, by the white material right next to it. That cancels
// the light source's colour and brightness and the camera's exposure, which is
// what lets a strip photographed today be compared with a chart photographed
// on another day.

export function srgbToLinear(v) {
  const c = v / 255;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

const LUT = new Float32Array(256);
for (let i = 0; i < 256; i++) LUT[i] = srgbToLinear(i);
export const linearLut = LUT;

function f(t) { return t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116; }

// Linear sRGB (1,1,1 = the reference white) to CIE Lab, D65.
export function linearToLab([r, g, b]) {
  const x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
  const y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b;
  const z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883;
  const fx = f(x), fy = f(y), fz = f(z);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

export function labToLinear([L, a, b]) {
  const fy = (L + 16) / 116, fx = fy + a / 500, fz = fy - b / 200;
  const inv = t => (t ** 3 > 216 / 24389 ? t ** 3 : (116 * t - 16) / (24389 / 27));
  const x = inv(fx) * 0.95047, y = inv(fy), z = inv(fz) * 1.08883;
  return [
    3.2404542 * x - 1.5371385 * y - 0.4985314 * z,
    -0.9692660 * x + 1.8760108 * y + 0.0415560 * z,
    0.0556434 * x - 0.2040259 * y + 1.0572252 * z,
  ];
}

export function labToCss(lab) {
  const enc = v => {
    const c = Math.min(1, Math.max(0, v));
    return Math.round(255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055));
  };
  const [r, g, b] = labToLinear(lab).map(enc);
  return `rgb(${r},${g},${b})`;
}

// Patch colour relative to its neighbouring white, as Lab.
export function whiteRelativeLab(patch, white) {
  return linearToLab([patch[0] / white[0], patch[1] / white[1], patch[2] / white[2]]);
}

// Lightness is the least trustworthy axis (wet pads darken, a curved bottle
// shades, glare lifts everything), so distances count it at reduced weight.
export const L_WEIGHT = 0.5;

export function distance(p, q, lWeight = L_WEIGHT) {
  const dL = (p[0] - q[0]) * lWeight, da = p[1] - q[1], db = p[2] - q[2];
  return Math.sqrt(dL * dL + da * da + db * db);
}

// Robust average of linear-RGB samples: per-channel mean of the middle of the
// distribution, so halftone dots, fibres, text and small glints do not skew it.
export function trimmedMean(samples, trim = 0.2) {
  const n = samples.length;
  if (!n) return null;
  const out = [0, 0, 0];
  for (let ch = 0; ch < 3; ch++) {
    const values = Float64Array.from(samples, s => s[ch]).sort();
    const lo = Math.floor(n * trim), hi = Math.max(lo + 1, Math.ceil(n * (1 - trim)));
    let sum = 0;
    for (let i = lo; i < hi; i++) sum += values[i];
    out[ch] = sum / (hi - lo);
  }
  return out;
}

// The white reference inside a window: the mean of the pixels whose weakest
// channel is strongest (white paper/plastic), ignoring the very top sliver
// where specular glints live.
export function whiteOf(samples, share = 0.12, skipTop = 0.01) {
  const n = samples.length;
  if (!n) return null;
  const ranked = samples.map(s => [Math.min(s[0], s[1], s[2]), s]).sort((a, b) => b[0] - a[0]);
  const from = Math.floor(n * skipTop), to = Math.max(from + 1, Math.floor(n * (skipTop + share)));
  const out = [0, 0, 0];
  for (let i = from; i < to; i++) for (let ch = 0; ch < 3; ch++) out[ch] += ranked[i][1][ch];
  return out.map(v => v / (to - from));
}
