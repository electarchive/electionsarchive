import { openapi } from "@orpc/openapi";
import { call, os } from "@orpc/server";
import * as v from "valibot";
import { fold } from "#lib/format.js";
import { polities } from "#lib/server/rpc/archive.js";

export const places = os
  .meta(
    openapi({
      method: "GET",
      path: "/polities/{id}/places",
      summary:
        "Find places inside a polity by name: its counties, municipalities, school districts, special districts, and other subdivisions, each with its election count and first and last election year. Names that start with q come first, then names with a word that starts with q, then any other match, larger places first within each group. Narrow by kind, and page with limit and offset; total counts every match",
    }),
  )
  .input(
    v.object({
      id: v.pipe(v.string(), v.uuid(), v.toLowerCase()),
      q: v.optional(v.pipe(v.string(), v.trim(), v.maxLength(100)), ""),
      kind: v.optional(v.pipe(v.string(), v.maxLength(40))),
      limit: v.optional(
        v.pipe(
          v.union([v.number(), v.pipe(v.string(), v.toNumber())]),
          v.integer(),
          v.minValue(1),
          v.maxValue(200),
        ),
        50,
      ),
      offset: v.optional(
        v.pipe(
          v.union([v.number(), v.pipe(v.string(), v.toNumber())]),
          v.integer(),
          v.minValue(0),
        ),
        0,
      ),
    }),
  )
  .output(
    v.object({
      polities: v.array(
        v.object({
          name: v.string(),
          path: v.nullable(v.string()),
          kind: v.string(),
          elections: v.number(),
          first: v.nullable(v.number()),
          last: v.nullable(v.number()),
          population: v.nullable(v.number()),
        }),
      ),
      total: v.number(),
    }),
  )
  .handler(async ({ input }) => {
    const found = await call(polities.get, { id: input.id });
    const needle = fold(input.q);
    const ranked = found.polities.flatMap((place) => {
      if (input.kind != null && place.kind !== input.kind) {
        return [];
      }
      const name = fold(place.name);
      const at = needle === "" ? 0 : name.indexOf(needle);
      if (at < 0) {
        return [];
      }
      const rank = at === 0 ? 0 : /[\s\-'(/]/u.test(name[at - 1] ?? "") ? 1 : 2;
      return [{ place, rank }];
    });
    const sorted = ranked.toSorted(
      (a, b) =>
        a.rank - b.rank ||
        (b.place.population ?? -1) - (a.place.population ?? -1) ||
        a.place.name.localeCompare(b.place.name),
    );
    return {
      polities: sorted
        .slice(input.offset, input.offset + input.limit)
        .map(({ place }) => place),
      total: sorted.length,
    };
  });
