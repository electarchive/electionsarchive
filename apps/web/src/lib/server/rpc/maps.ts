import { openapi } from "@orpc/openapi";
import * as v from "valibot";
import { os } from "#lib/server/cache.js";
import { boxes } from "#lib/server/plane.js";

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
    .output(
      v.array(
        v.object({
          name: v.string(),
          x: v.number(),
          y: v.number(),
          width: v.number(),
          height: v.number(),
          units: v.array(v.object({ k: v.number(), d: v.string() })),
        }),
      ),
    )
    .handler(async ({ input }) => boxes(input.scheme, input.day, input.ks)),
};
