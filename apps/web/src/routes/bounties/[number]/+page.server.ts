import { READ_ONLY } from "$app/env/public";
import { error, fail, redirect } from "@sveltejs/kit";
import { claim, get, release } from "#lib/server/bounties.js";
import { attempt } from "#lib/server/discussion-actions.js";
import { watching } from "#lib/server/feeds.js";
import type { Actions, PageServerLoad } from "./$types";

const parse = (value: string) => {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) {
    error(404);
  }
  return number;
};

export const load: PageServerLoad = async ({ params, locals, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const found = await get(parse(params.number));
  if (found == null) {
    error(404, "There is no bounty with that number.");
  }
  return {
    bounty: found,
    watch:
      locals.user == null || READ_ONLY
        ? null
        : { watching: await watching(locals.user.id, "bounty", found.id) },
    mine: locals.user != null && found.claimed_by === locals.user.id,
    claimer:
      locals.user == null
        ? ("signed_out" as const)
        : locals.user.emailVerified
          ? ("ready" as const)
          : ("unverified" as const),
  };
};

export const actions: Actions = {
  claim: async ({ params, locals, url }) => {
    if (locals.user == null) {
      redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
    }
    const { id } = locals.user;
    return attempt(async () => claim(id, parse(params.number)));
  },
  release: async ({ params, locals }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to release a bounty." });
    }
    await release(locals.user.id, parse(params.number));
    return null;
  },
};
