import { palette } from "#lib/palette.svelte.js";

const NEUTRALS = [78, 56, 36].map(
  (amount) =>
    `color-mix(in oklab, var(--muted-foreground) ${amount}%, var(--card))`,
);

const SIDES = new Map([
  ["answer:leave", "#005ea7"],
  ["answer:remain", "#f7c600"],
  ["answer:leave the european union", "#005ea7"],
  ["answer:remain a member of the european union", "#f7c600"],
]);

function oklch(hex: string) {
  const digits =
    hex.length === 4
      ? [...hex.slice(1)].map((digit) => digit + digit)
      : [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)];
  const [r = 0, g = 0, b = 0] = digits.map((pair) => {
    const value = Number.parseInt(pair, 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const lightness = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return {
    l: lightness,
    c: Math.hypot(a, bb),
    h: ((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360,
  };
}

const SAFE = [
  "#e69f00",
  "#56b4e9",
  "#009e73",
  "#f0e442",
  "#0072b2",
  "#d55e00",
  "#cc79a7",
].map((hex) => oklch(hex));

export const WITHIN = Symbol("within");

export function independent(rank: number) {
  return (
    [NEUTRALS[1], NEUTRALS[0], NEUTRALS[2]][rank % NEUTRALS.length] ??
    "var(--muted-foreground)"
  );
}

export function swatch(color: string | null | undefined, key: string) {
  const chosen =
    (key.startsWith("party:") ? palette.overrides[key.slice(6)] : undefined) ??
    color ??
    SIDES.get(key);
  if (key === "independent" && chosen == null) {
    return independent(0);
  }
  if (chosen == null && (key === "" || key === "none" || key === "party:")) {
    return "color-mix(in oklab, var(--muted-foreground) 20%, var(--card))";
  }
  if (chosen != null && NEUTRALS.includes(chosen)) {
    return chosen;
  }
  if (chosen != null) {
    let base = chosen;
    const shade =
      palette.isSafe && /^#[\da-f]{3}(?:[\da-f]{3})?$/i.test(chosen)
        ? oklch(chosen)
        : null;
    if (shade != null && shade.c >= 0.04) {
      const gap = (hue: number) => {
        const turn = Math.abs(hue - shade.h);
        return Math.min(turn, 360 - turn);
      };
      const near = SAFE.toSorted((x, y) => gap(x.h) - gap(y.h))[0];
      if (near != null) {
        base = `oklch(${shade.l.toFixed(3)} ${near.c.toFixed(3)} ${near.h.toFixed(1)})`;
      }
    }
    return `light-dark(oklch(from ${base} min(l, 0.86) c h), oklch(from ${base} max(l, 0.52) c h))`;
  }
  if (/^answer:(?:yes|sì|evet|for|approve|i agree)(?:$|\s)/u.test(key)) {
    return "var(--yes)";
  }
  if (/^answer:(?:no|hayır|against|reject|i do not agree)(?:$|\s)/u.test(key)) {
    return "var(--no)";
  }
  let hash = 0;
  for (const character of key) {
    hash = (hash * 31 + (character.codePointAt(0) ?? 0)) % 9973;
  }
  return NEUTRALS[hash % NEUTRALS.length] ?? "var(--muted-foreground)";
}

export function era<T extends { party: string; from: string }>(
  list: Array<T>,
  party: string | null,
  day: string | null,
) {
  let found: T | undefined;
  for (const entry of list) {
    if (
      entry.party === party &&
      (found == null || (day != null && entry.from <= day))
    ) {
      found = entry;
    }
  }
  return found;
}
