import { openapi } from "@orpc/openapi";
import * as v from "valibot";
import {
  recent,
  RecentFilters,
  setWatch,
  WATCH_KINDS,
  watchlist,
} from "#lib/server/feeds.js";
import { keyed, reader, run, writer } from "#lib/server/rpc/keyed.js";

const Target = v.object({
  kind: v.picklist(WATCH_KINDS),
  id: v.pipe(v.string(), v.uuid()),
});

export const recentChanges = {
  list: reader
    .meta(
      openapi({
        method: "GET",
        path: "/recent",
        summary:
          "Recent changes, newest first, 50 a page: pipeline imports grouped by pipeline, polity, and day, plus submitted proposals, decisions, and discussion; filter by polity path (with the places within it), author kind, or tier, and pass before=next for older entries",
      }),
    )
    .input(RecentFilters)
    .handler(async ({ input }) => run(async () => recent(input))),
};

export const watches = {
  list: keyed
    .meta(
      openapi({
        method: "GET",
        path: "/watchlist",
        summary:
          "Your watchlist: recent changes to the entities, polities, proposals, and contributors you watch, others' activity on your proposals in the last 14 days, and the watched targets",
      }),
    )
    .input(
      v.object({ before: v.optional(v.pipe(v.string(), v.isoTimestamp())) }),
    )
    .handler(async ({ input, context: { owner } }) =>
      run(async () => watchlist(owner.userId, input.before ?? null)),
    ),
  add: writer
    .meta(
      openapi({
        method: "PUT",
        path: "/watch",
        summary:
          "Watch an entity, a polity (with the places within it), a submitted proposal, or a contributor by user ID",
      }),
    )
    .input(Target)
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        await setWatch(owner.userId, input.kind, input.id, true);
        return { kind: input.kind, id: input.id, watching: true };
      }),
    ),
  remove: writer
    .meta(
      openapi({
        method: "DELETE",
        path: "/watch",
        summary: "Stop watching an entity, polity, proposal, or contributor",
      }),
    )
    .input(Target)
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        await setWatch(owner.userId, input.kind, input.id, false);
        return { kind: input.kind, id: input.id, watching: false };
      }),
    ),
};
