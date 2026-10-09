// Seeded randomness and a stable hash, so every visitor sees the same sample travellers.

export function mulberry32(seed) {
  let a = seed;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// FNV-1a → [0, 1). Same role as stable_hash() in the design: a holdout or control
// assignment that never changes for the same customer, action and trip.
export function stableHash(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 4294967296;
}

export function weighted(rand, items) {
  const total = items.reduce((s, i) => s + i.w, 0);
  let r = rand() * total;
  for (const item of items) {
    r -= item.w;
    if (r <= 0) return item;
  }
  return items[items.length - 1];
}

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
