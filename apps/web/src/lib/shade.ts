declare const closerIsPaler: unique symbol;

export type Safety = number & { readonly [closerIsPaler]: true };

export type Ramp = { edges: Array<number>; steps: Array<Safety> };

export function safety(fraction: number) {
  return Math.min(1, Math.max(0, fraction)) as Safety;
}

export const SAFE = safety(1);

function ramp(edges: Array<number>, levels: Array<number>): Ramp {
  return { edges, steps: levels.map((level) => safety(level)) };
}

export const MARGIN = ramp(
  [0.05, 0.1, 0.2, 0.3, 0.45],
  [0.26, 0.4, 0.54, 0.68, 0.84, 1],
);
export const SWING = ramp([0.02, 0.05, 0.1], [0.34, 0.54, 0.76, 1]);
export const SHARE = ramp(
  [0.3, 0.4, 0.5, 0.6, 0.7],
  [0.14, 0.3, 0.48, 0.66, 0.84, 1],
);
export const QUINTILE = ramp([], [0.16, 0.36, 0.56, 0.78, 1]);

export function bin(value: number, edges: Array<number>, isStrict = false) {
  let index = 0;
  for (const edge of edges) {
    if (isStrict ? value > edge : value >= edge) {
      index += 1;
    }
  }
  return index;
}

export function rampSafety(
  scale: Ramp,
  value: number,
  edges = scale.edges,
  isStrict = false,
) {
  return scale.steps[bin(value, edges, isStrict)] ?? SAFE;
}

export function marginSafety(margin: number) {
  return rampSafety(MARGIN, Math.abs(margin));
}

export function swingSafety(swing: number) {
  return rampSafety(SWING, Math.abs(swing));
}

export function shareSafety(share: number) {
  return rampSafety(SHARE, share);
}

export function leadSafety(margin: number, isCalled = false) {
  return isCalled
    ? SAFE
    : safety(0.3 + 0.6 * Math.min(Math.abs(margin) / 0.3, 1));
}

export function bands(scale: Ramp) {
  return scale.steps.map((level, index) => ({
    from: index === 0 ? 0 : (scale.edges[index - 1] ?? 0),
    to: scale.edges[index] ?? null,
    safety: level,
  }));
}

export function marginShade(color: string, level: Safety) {
  return `color-mix(in oklab, ${color} ${Math.round(level * 100)}%, var(--map-low))`;
}
