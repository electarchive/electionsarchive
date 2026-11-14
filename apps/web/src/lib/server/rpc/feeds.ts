import { openapi } from "@orpc/openapi";
import { recent, RecentFilters } from "#lib/server/feeds.js";
import { reader, run } from "#lib/server/rpc/keyed.js";

export const recentChanges = {
  list: reader
    .meta(
      openapi({
        method: "GET",
        path: "/recent",
        summary:
          "Recent pipeline imports, newest first, 50 a page, grouped by pipeline, polity, and day; filter by polity path (with the places within it), and pass before=next for older entries",
      }),
    )
    .input(RecentFilters)
    .handler(async ({ input }) => run(async () => recent(input))),
};
