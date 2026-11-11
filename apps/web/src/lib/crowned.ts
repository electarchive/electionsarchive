import type { InferRouterOutputs } from "@orpc/server";
import type { router } from "#lib/server/rpc/router.js";

type Place = InferRouterOutputs<
  typeof router
>["elections"]["winners"]["maps"][number]["places"];

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
