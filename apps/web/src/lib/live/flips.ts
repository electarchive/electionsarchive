import { marginShade, safety } from "#lib/shade.js";

export type Flip = "flipped" | "leading" | "held";

export type Shift = { flip: Flip; from: string; to: string };

export type Swing = {
  flipped: number;
  leading: number;
  parties: Array<{ id: string; flipped: number; leading: number }>;
};

export const FLIP_ORDER: Record<Flip, number> = {
  flipped: 2,
  leading: 1,
  held: 0,
};

export function shift({
  held,
  to,
  isCalled,
  isCounting,
}: {
  held: string | null;
  to: string | null;
  isCalled: boolean;
  isCounting: boolean;
}): Shift | null {
  if (held == null || to == null || (!isCalled && !isCounting)) {
    return null;
  }
  const flip: Flip = to === held ? "held" : isCalled ? "flipped" : "leading";
  return { flip, from: held, to };
}

export function isChanging(found: Shift | null) {
  return found != null && found.flip !== "held";
}

export function swing(shifts: Iterable<Shift | null>): Swing {
  const parties = new Map<string, { flipped: number; leading: number }>();
  let flipped = 0;
  let leading = 0;
  for (const found of shifts) {
    if (found == null || found.flip === "held") {
      continue;
    }
    const party = parties.get(found.to) ?? { flipped: 0, leading: 0 };
    party[found.flip] += 1;
    parties.set(found.to, party);
    if (found.flip === "flipped") {
      flipped += 1;
    } else {
      leading += 1;
    }
  }
  return {
    flipped,
    leading,
    parties: [...parties]
      .map(([id, counts]) => ({ id, ...counts }))
      .toSorted(
        (a, b) =>
          b.flipped - a.flipped ||
          b.leading - a.leading ||
          a.id.localeCompare(b.id),
      ),
  };
}

export function stripes(color: string) {
  return `repeating-linear-gradient(135deg, ${color} 0 1.5px, transparent 1.5px 4px)`;
}

export function hatch(color: string, flip: "flipped" | "leading") {
  return flip === "flipped"
    ? {
        fill: `color-mix(in oklab, ${color} 84%, black)`,
        stripes: stripes(
          `color-mix(in oklab, ${color} 45%, var(--foreground))`,
        ),
      }
    : {
        fill: marginShade(color, safety(0.34)),
        stripes: stripes(marginShade(color, safety(0.68))),
      };
}

export function ink(fill: string) {
  return `oklch(from ${fill} clamp(0, (0.7 - l) * 1000, 1) 0 0)`;
}
