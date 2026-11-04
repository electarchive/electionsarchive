import type { Sheet } from "#lib/atlas/data.js";
import { largest } from "#lib/map/engine.svelte.js";
import {
  bands,
  MARGIN,
  marginSafety,
  marginShade,
  SAFE,
  safety,
  SHARE,
  shareSafety,
  SWING,
  swingSafety,
} from "#lib/shade.js";

export const MODES = [
  "winner",
  "split",
  "share",
  "votes",
  "swing",
  "flips",
  "lean",
] as const;
export type Mode = (typeof MODES)[number];

export type Lens = {
  mode: Mode;
  first: string | null;
  second: string | null;
  whom: string | null;
  isBanded: boolean;
  isCompared: boolean;
  baseline: number;
  range: [number, number] | null;
  only: string | null;
  isFlippedOnly: boolean;
  tone: (key: string | null | undefined) => string;
};

export type Reading = ReturnType<typeof read>;

export const DIM =
  "color-mix(in oklab, var(--muted-foreground) 11%, var(--card))";
export const QUIET =
  "color-mix(in oklab, var(--muted-foreground) 15%, var(--card))";
const ABSENT = "color-mix(in oklab, var(--muted-foreground) 6%, var(--card))";
const NEUTRAL = "var(--muted-foreground)";

const indexes = new WeakMap<Array<{ key: string }>, Map<string, number>>();

export function slot(set: Sheet, key: string | null | undefined) {
  if (key == null) {
    return -1;
  }
  let found = indexes.get(set.legend);
  if (found == null) {
    found = new Map(set.legend.map((item, index) => [item.key, index]));
    indexes.set(set.legend, found);
  }
  return found.get(key) ?? -1;
}

export function scale(mode: Mode) {
  switch (mode) {
    case "share": {
      return { domain: [0, 1] as [number, number], step: 0.05 };
    }
    case "swing": {
      return { domain: [-0.3, 0.3] as [number, number], step: 0.025 };
    }
    case "lean": {
      return { domain: [-0.6, 0.6] as [number, number], step: 0.04 };
    }
    default: {
      return { domain: [-0.8, 0.8] as [number, number], step: 0.05 };
    }
  }
}

export function share(set: Sheet, i: number, key: string | null | undefined) {
  const index = slot(set, key);
  const value = index < 0 ? 0 : set.places.shares[index]?.[i];
  return value == null ? null : value / 10_000;
}

export function was(set: Sheet, i: number, key: string | null | undefined) {
  const index = slot(set, key);
  const value = index < 0 ? null : (set.places.before[index]?.[i] ?? null);
  return value == null ? null : value / 10_000;
}

export function read(set: Sheet, i: number, lens: Lens) {
  const columns = set.places.shares;
  let top = 0;
  let next = 0;
  for (const column of columns) {
    const value = (column[i] ?? 0) / 10_000;
    if (value > top) {
      next = top;
      top = value;
    } else if (value > next) {
      next = value;
    }
  }
  const a = share(set, i, lens.first);
  const b = share(set, i, lens.second);
  const margin = (a ?? 0) - (b ?? 0);
  const then = [was(set, i, lens.first), was(set, i, lens.second)];
  const leader = set.places.leader[i] ?? null;
  const prior = set.places.prior[i] ?? null;
  const swing =
    then[0] == null || then[1] == null ? null : margin - (then[0] - then[1]);
  const mine = share(set, i, lens.whom);
  const value =
    lens.mode === "share"
      ? mine
      : lens.mode === "swing"
        ? swing
        : lens.mode === "lean"
          ? a == null || b == null || leader == null
            ? null
            : margin - lens.baseline
          : leader == null
            ? null
            : margin;
  const held = set.places.held?.[i] ?? null;
  return {
    leader,
    held,
    owner:
      held != null && !lens.isBanded
        ? (largest(held, (index) => columns[index]?.[i] ?? 0) ?? leader)
        : leader,
    lead: top - next,
    margin,
    isMeasured: a != null && b != null,
    swing,
    value,
    isFlip: prior != null && leader != null && prior !== leader,
    isAbsent: lens.mode === "share" && leader != null && mine == null,
    isUnmatched:
      lens.isCompared &&
      leader != null &&
      set.places.before.every((column) => column[i] == null),
  };
}

export function admits(set: Sheet, reading: Reading, lens: Lens) {
  const { domain } = scale(lens.mode);
  const [low, high] = lens.range ?? [-Infinity, Infinity];
  const clamped =
    reading.value == null
      ? null
      : Math.min(domain[1], Math.max(domain[0], reading.value));
  return (
    (lens.range == null ||
      (clamped != null && clamped >= low - 1e-9 && clamped <= high + 1e-9)) &&
    (lens.only == null ||
      (reading.leader != null &&
        set.legend[reading.leader]?.key === lens.only) ||
      (reading.held?.some(([index]) => set.legend[index]?.key === lens.only) ??
        false)) &&
    (!lens.isFlippedOnly || !lens.isCompared || reading.isFlip)
  );
}

export function hue(set: Sheet, index: number | null, lens: Lens) {
  const item = index == null ? null : set.legend[index];
  return item == null ? NEUTRAL : lens.tone(item.key);
}

export function tone(
  set: Sheet,
  i: number,
  lens: Lens,
  reading = read(set, i, lens),
) {
  if (reading.leader == null || !admits(set, reading, lens)) {
    return DIM;
  }
  if (reading.isAbsent) {
    return ABSENT;
  }
  switch (lens.mode) {
    case "share": {
      return marginShade(lens.tone(lens.whom), shareSafety(reading.value ?? 0));
    }
    case "swing": {
      return reading.swing == null
        ? DIM
        : marginShade(
            lens.tone(reading.swing >= 0 ? lens.first : lens.second),
            swingSafety(reading.swing),
          );
    }
    case "lean": {
      return reading.value == null
        ? DIM
        : marginShade(
            lens.tone(reading.value >= 0 ? lens.first : lens.second),
            marginSafety(reading.value),
          );
    }
    case "flips": {
      return reading.isUnmatched
        ? DIM
        : marginShade(
            hue(set, reading.leader, lens),
            safety(reading.isFlip ? 1 : 0.16),
          );
    }
    default: {
      const level =
        set.places.valid[i] === 0 || (reading.held != null && !lens.isBanded)
          ? SAFE
          : marginSafety(reading.lead);
      return marginShade(
        hue(set, reading.owner, lens),
        lens.mode === "votes" ? safety(Math.max(0.54, level)) : level,
      );
    }
  }
}

export function band(middle: number, lens: Lens) {
  if (lens.mode === "share") {
    return marginShade(lens.tone(lens.whom), shareSafety(middle));
  }
  return marginShade(
    lens.tone(middle >= 0 ? lens.first : lens.second),
    lens.mode === "swing" ? swingSafety(middle) : marginSafety(middle),
  );
}

export function strengths(lens: Pick<Lens, "mode">) {
  return bands(
    lens.mode === "share" ? SHARE : lens.mode === "swing" ? SWING : MARGIN,
  );
}
