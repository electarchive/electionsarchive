import { openapi } from "@orpc/openapi";
import * as v from "valibot";
import { os } from "#lib/server/cache.js";
import { boxes, vintage, zooms } from "#lib/server/plane.js";
import { names } from "#lib/server/rpc/archive.js";

const Tiles = v.pipe(
  v.string(),
  v.regex(/^[a-z]{2,5}\.[a-z0-9_]+\/[\w:.-]+$/u),
);

const Boxes = v.array(
  v.object({
    name: v.string(),
    x: v.number(),
    y: v.number(),
    width: v.number(),
    height: v.number(),
    units: v.array(v.object({ k: v.number(), d: v.string() })),
  }),
);

export const maps = {
  insets: os
    .meta(
      openapi({
        method: "GET",
        path: "/maps/{scheme}/insets",
        summary:
          "Draw the places a national map sets apart in fixed boxes, such as Alaska and Hawaii on a map of the United States: each box's name, its frame in map coordinates, and a simplified SVG path for every place in it",
      }),
    )
    .input(
      v.object({
        scheme: v.pipe(v.string(), v.regex(/^[a-z]{2,5}\.[a-z0-9_]+$/u)),
        day: v.pipe(v.string(), v.isoDate()),
        ks: v.pipe(v.array(v.pipe(v.number(), v.integer())), v.maxLength(5000)),
      }),
    )
    .output(Boxes)
    .handler(async ({ input }) => boxes(input.scheme, input.day, input.ks)),
  detached: os
    .meta(
      openapi({
        method: "GET",
        path: "/maps/detached",
        summary:
          "Draw the overseas places a national map lists apart, such as the constituencies of Guadeloupe and Guyane on a map of France, grouped by territory: each territory's name, its frame in map coordinates, and a simplified SVG path for every place in it",
      }),
    )
    .input(
      v.object({
        tiles: Tiles,
        ks: v.pipe(v.array(v.pipe(v.number(), v.integer())), v.maxLength(500)),
      }),
    )
    .output(Boxes)
    .handler(async ({ input }) => {
      const held = await vintage(input.tiles);
      return held == null ? [] : boxes(held.scheme, held.day, input.ks);
    }),
  zooms: os
    .meta(
      openapi({
        method: "GET",
        path: "/maps/zooms",
        summary:
          "Find the dense clusters of small places on a map, such as Paris and its inner suburbs on a map of French constituencies, and draw each one at its own scale: its name, its frame in map and geographic coordinates, the places at its core, the cluster it sits inside, and a simplified SVG path for every place in its frame",
      }),
    )
    .input(
      v.object({
        tiles: Tiles,
        ks: v.pipe(v.array(v.pipe(v.number(), v.integer())), v.maxLength(5000)),
      }),
    )
    .output(
      v.array(
        v.object({
          name: v.string(),
          kind: v.picklist(["exact", "around", "inside"]),
          within: v.nullable(v.number()),
          core: v.array(v.number()),
          frame: v.object({
            west: v.number(),
            south: v.number(),
            east: v.number(),
            north: v.number(),
          }),
          x: v.number(),
          y: v.number(),
          width: v.number(),
          height: v.number(),
          units: v.array(v.object({ k: v.number(), d: v.string() })),
        }),
      ),
    )
    .handler(async ({ input }) => {
      const { day, ids, zones } = await zooms(input.tiles, input.ks);
      const labels = await names(
        zones.flatMap((zone) => (zone.place == null ? [] : [zone.place])),
        day,
      );
      const isUnnamed = zones.some((zone) => zone.kind === "inside");
      const units = isUnnamed ? await names(ids, day) : null;
      const words = (id: string) =>
        new Set(units?.full.get(id)?.match(/\p{Lu}[\p{L}'’]{3,}/gu));
      const counted = new Map<string, number>();
      for (const id of isUnnamed ? ids : []) {
        for (const word of words(id)) {
          counted.set(word, (counted.get(word) ?? 0) + 1);
        }
      }
      const common = (cores: Array<string>) => {
        const inside = new Map<string, number>();
        for (const id of cores) {
          for (const word of words(id)) {
            inside.set(word, (inside.get(word) ?? 0) + 1);
          }
        }
        const [word] =
          inside
            .entries()
            .filter(
              ([word, count]) =>
                count >= Math.max(3, cores.length / 4) &&
                count >= (counted.get(word) ?? 0) * 0.75,
            )
            .toArray()
            .toSorted((a, b) => b[1] - a[1])[0] ?? [];
        return word ?? "";
      };
      return zones.map(({ place, cores, kind, ...zone }) => {
        const word = kind === "inside" ? common(cores) : "";
        return {
          ...zone,
          kind: word === "" ? kind : ("exact" as const),
          name:
            word === ""
              ? place == null
                ? ""
                : (labels.full.get(place) ?? "")
              : word,
        };
      });
    }),
};
