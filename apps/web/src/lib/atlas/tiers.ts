import type { Mosaic, Sheet, Shell } from "#lib/atlas/data.js";

export type Tier = {
  code: string;
  noun: string;
  count: number;
  tiles: string | null;
  kind: "level" | "mosaic" | "parts";
};

export const FINE = "fine";
const DENSE = 7;
const FADE = 0.4;
const HEAVY = 12_000;

function tiled(shell: Shell) {
  return shell.levels.filter(
    (level) => level.tiles != null && !level.code.startsWith("world."),
  );
}

export function fine(
  shell: Shell,
  mosaic: Mosaic | null,
  sample: Sheet | null,
): Tier | null {
  const finest = mosaic?.layers.at(-1);
  if (mosaic != null && finest != null) {
    return {
      code: FINE,
      noun: finest.noun,
      count: mosaic.set.places.k.length,
      tiles: null,
      kind: "mosaic",
    };
  }
  const level = sample?.levels.find((entry) => entry.code === sample.level);
  if (sample == null || level == null || !isParted(shell)) {
    return null;
  }
  return {
    code: FINE,
    noun: level.noun,
    count: sample.places.k.length * shell.parts.length,
    tiles: null,
    kind: "parts",
  };
}

export function isParted(shell: Shell) {
  return (
    shell.parts.length > 0 &&
    shell.levels.find((level) => level.code === shell.level)?.count ===
      shell.parts.length
  );
}

export function choices(
  shell: Shell,
  mosaic: Mosaic | null,
  sample: Sheet | null,
) {
  const found: Array<Tier> = shell.levels
    .filter((level) => level.tiles != null)
    .map((level) => ({ ...level, kind: "level" }));
  const extra = fine(shell, mosaic, sample);
  return extra == null ? found : [...found, extra];
}

export function chain(shell: Shell) {
  const levels = tiled(shell).filter(
    (level) => level.count <= HEAVY || level.code === shell.level,
  );
  const last = levels.at(-1);
  const near = levels.length > 2 ? levels.at(-2) : undefined;
  const end =
    near != null && last != null && near.count * 1.5 >= last.count
      ? near
      : last;
  const wanted = new Set([levels[0]?.code, end?.code, shell.level]);
  const found: Array<Tier> = levels
    .filter((level) => wanted.has(level.code))
    .map((level) => ({ ...level, kind: "level" }));
  return found;
}

export function starts(tiers: Array<Tier>, fit: number, area: number) {
  const found: Array<number> = [];
  let floor = -Infinity;
  for (const [index, tier] of tiers.entries()) {
    const at =
      index === 0
        ? -Infinity
        : Math.max(
            floor + 0.6,
            fit +
              0.5 * Math.log2((DENSE * DENSE * tier.count) / Math.max(1, area)),
          );
    found.push(at);
    floor = at;
  }
  return found;
}

export function active(
  begins: Array<number>,
  zoom: number,
  last: number | null,
) {
  let found = 0;
  for (const [index, start] of begins.entries()) {
    const slack =
      FADE / 2 + (last == null ? 0 : (FADE / 8) * (index > last ? 1 : -1));
    if (zoom >= start + slack) {
      found = index;
    }
  }
  return found;
}

export function fade(start: number) {
  return Number.isFinite(start)
    ? ([
        "interpolate",
        ["linear"],
        ["zoom"],
        start,
        0,
        start + FADE,
        1,
      ] as const)
    : 1;
}
