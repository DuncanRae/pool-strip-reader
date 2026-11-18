// Minimal uncompressed 24/32-bit BMP reader and writer for the offline tools.
// (macOS `sips` converts the phone's HEIC photos to sRGB BMP.)
import fs from 'node:fs';

export function readBmp(path) {
  const b = fs.readFileSync(path);
  if (b.toString('latin1', 0, 2) !== 'BM') throw new Error('Not a BMP file: ' + path);
  const offset = b.readUInt32LE(10), width = b.readInt32LE(18), rawHeight = b.readInt32LE(22);
  const bpp = b.readUInt16LE(28), compression = b.readUInt32LE(30);
  if (![24, 32].includes(bpp) || ![0, 3].includes(compression)) throw new Error('Unsupported BMP encoding.');
  const height = Math.abs(rawHeight), topDown = rawHeight < 0, bytes = bpp / 8;
  const stride = Math.floor((bpp * width + 31) / 32) * 4;
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    const row = offset + (topDown ? y : height - 1 - y) * stride;
    for (let x = 0; x < width; x++) {
      const s = row + x * bytes, d = (y * width + x) * 4;
      data[d] = b[s + 2]; data[d + 1] = b[s + 1]; data[d + 2] = b[s]; data[d + 3] = 255;
    }
  }
  return { width, height, data };
}

export function writeBmp(path, { width, height, data }) {
  const stride = Math.floor((24 * width + 31) / 32) * 4;
  const out = Buffer.alloc(54 + stride * height);
  out.write('BM'); out.writeUInt32LE(out.length, 2); out.writeUInt32LE(54, 10);
  out.writeUInt32LE(40, 14); out.writeInt32LE(width, 18); out.writeInt32LE(-height, 22);
  out.writeUInt16LE(1, 26); out.writeUInt16LE(24, 28); out.writeUInt32LE(stride * height, 34);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const s = (y * width + x) * 4, d = 54 + y * stride + x * 3;
    out[d] = data[s + 2]; out[d + 1] = data[s + 1]; out[d + 2] = data[s];
  }
  fs.writeFileSync(path, out);
}
