import { openapi } from "@orpc/openapi";
import * as v from "valibot";
import {
  contributor,
  Handle,
  contributors as list,
} from "#lib/server/contributors.js";
import { reader, run } from "#lib/server/rpc/keyed.js";

export const contributors = {
  list: reader
    .meta(
      openapi({
        method: "GET",
        path: "/contributors",
        summary:
          "Contributors by handle, 100 a page: display name, affiliation, member since, and counts of submitted and accepted proposals; q matches a handle prefix or part of a display name, and after continues from next",
      }),
    )
    .input(
      v.object({
        q: v.optional(v.pipe(v.string(), v.trim(), v.maxLength(60))),
        after: v.optional(Handle),
      }),
    )
    .handler(async ({ input }) =>
      run(async () =>
        list({
          q: input.q == null || input.q === "" ? null : input.q,
          after: input.after ?? null,
        }),
      ),
    ),
  get: reader
    .meta(
      openapi({
        method: "GET",
        path: "/contributors/{handle}",
        summary:
          "A contributor's public profile by handle: display name, affiliation, bio, links, member since, counts of accepted, open, rejected, and reverted proposals, their submitted proposals with states, and the agents that proposed for them",
      }),
    )
    .input(v.object({ handle: Handle }))
    .handler(async ({ input }) =>
      run(async () => {
        const { userId: _user, ...found } = await contributor(input.handle);
        return found;
      }),
    ),
};
