import { openapi } from "@orpc/openapi";
import * as v from "valibot";
import { Before, change, history } from "#lib/server/history.js";
import { reader, run } from "#lib/server/rpc/keyed.js";
import { PublicId } from "#lib/server/talk.js";

export const histories = {
  list: reader
    .meta(
      openapi({
        method: "GET",
        path: "/history/{id}",
        summary:
          "A record's history by its public ID: every published changeset that touched it, pipeline imports and contributions alike, newest first, 50 a page, with its author, summary, role, and proposal; pass before=next for older entries",
      }),
    )
    .input(v.object({ id: PublicId, before: v.optional(Before) }))
    .handler(async ({ input }) =>
      run(async () => history(input.id, input.before ?? null)),
    ),
  change: reader
    .meta(
      openapi({
        method: "GET",
        path: "/history/{id}/{changeset}",
        summary:
          "What one changeset did to a record: each added, changed, or dropped row in its own tables, field by field, from the history tables, with referenced records named",
      }),
    )
    .input(v.object({ id: PublicId, changeset: v.pipe(v.string(), v.uuid()) }))
    .handler(async ({ input }) =>
      run(async () => change(input.id, input.changeset)),
    ),
};
