import type { InferRouterOutputs } from "@orpc/server";
import type { router } from "#lib/server/rpc/router.js";

type Wire = InferRouterOutputs<typeof router>["elections"]["winners"];

type Place = Wire["maps"][number]["places"] & {
  legend: ReturnType<typeof withLegend>[number]["places"]["legend"];
};

export function withLegend(winners: Wire | undefined) {
  const legend = (winners?.legend ?? []).map((item) => ({
    ...item,
    short: item.short ?? null,
    color: item.color ?? null,
    person: item.person ?? null,
    party: item.party ?? null,
    key:
      item.party == null
        ? `person:${item.person ?? ""}`
        : `party:${item.party}`,
  }));
  return (winners?.maps ?? []).map((entry) => ({
    ...entry,
    places: { ...entry.places, legend },
  }));
}

export function withEntries(winners: Wire | undefined) {
  const entries = winners?.entries ?? [];
  return (winners?.rows ?? []).map((row) => {
    const { entry, prior, ...rest } = row;
    const before = prior == null ? undefined : entries[prior];
    return {
      ...rest,
      party: entries[entry]?.party,
      short: entries[entry]?.short,
      color: entries[entry]?.color,
      prior:
        prior == null
          ? undefined
          : before?.party == null
            ? "person:"
            : `party:${before.party}`,
    };
  });
}

export function crowned(places: Place) {
  const size = places.legend.length;
  const dense = (spread: Array<[number, number]>) => {
    const found = new Map(spread);
    return Array.from({ length: size }, (_, index) => found.get(index) ?? 0);
  };
  return {
    ...places,
    rows: places.rows.map((row) => ({
      ...row,
      shares: dense(row.shares),
      earlier: row.earlier == null ? null : dense(row.earlier),
    })),
  };
}
