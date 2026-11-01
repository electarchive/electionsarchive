export function hash(text: string) {
  let value = 2_166_136_261;
  for (const character of text) {
    value ^= character.codePointAt(0) ?? 0;
    value = Math.imul(value, 16_777_619);
  }
  return value >>> 0;
}

export function stream(seed: number) {
  let state = seed >>> 0;
  const next = () => {
    state = (state + 0x6D_2B_79_F5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
  let spare: number | null = null;
  const normal = () => {
    if (spare != null) {
      const held = spare;
      spare = null;
      return held;
    }
    const u = Math.max(next(), 1e-12);
    const v = next();
    const radius = Math.sqrt(-2 * Math.log(u));
    spare = radius * Math.sin(2 * Math.PI * v);
    return radius * Math.cos(2 * Math.PI * v);
  };
  return { next, normal };
}

export function quantile(sorted: ArrayLike<number>, q: number) {
  if (sorted.length === 0) {
    return 0;
  }
  const position = (sorted.length - 1) * q;
  const low = Math.floor(position);
  const high = Math.ceil(position);
  const a = sorted[low] ?? 0;
  const b = sorted[high] ?? a;
  return a + (b - a) * (position - low);
}
