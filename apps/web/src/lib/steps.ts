type Seats = Array<{ party: string | null; seats: number }>;
type Near = Record<string, string | null>;

export const fold = <T extends { parties: Seats; near: Near }>(
  steps: Array<T>,
) => {
  let held = new Map<string | null, number>();
  let near: Near | null = null;
  return steps.map(({ parties, near: nearby, ...rest }) => {
    const next = new Map<string | null, number>();
    const changed: Seats = [];
    for (const { party, seats } of parties) {
      next.set(party, seats);
      if (held.get(party) !== seats) {
        changed.push({ party, seats });
      }
    }
    for (const party of held.keys()) {
      if (!next.has(party)) {
        changed.push({ party, seats: 0 });
      }
    }
    const isKept = nearby === near;
    held = next;
    near = nearby;
    return { ...rest, changed, near: isKept ? null : nearby };
  });
};

export const unfold = <T extends { changed: Seats; near: Near | null }>(
  steps: Array<T>,
) => {
  const held = new Map<string | null, number>();
  let near: Near = {};
  return steps.map(({ changed, near: nearby, ...rest }) => {
    for (const { party, seats } of changed) {
      if (seats > 0) {
        held.set(party, seats);
      } else {
        held.delete(party);
      }
    }
    near = nearby ?? near;
    return {
      ...rest,
      near,
      parties: held
        .entries()
        .map(([party, seats]) => ({ party, seats }))
        .toArray()
        .toSorted(
          (a, b) =>
            b.seats - a.seats || (a.party ?? "").localeCompare(b.party ?? ""),
        ),
    };
  });
};
