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
