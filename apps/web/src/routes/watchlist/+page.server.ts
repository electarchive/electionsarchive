import { fail, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { field, Id } from "#lib/server/discussion-actions.js";
import {
  feedToken,
  setWatch,
  WATCH_KINDS,
  watchlist,
} from "#lib/server/feeds.js";
import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  if (locals.user == null) {
    redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
  }
  const before = url.searchParams.get("before");
  const [found, token] = await Promise.all([
    watchlist(
      locals.user.id,
      before != null && v.is(v.pipe(v.string(), v.isoTimestamp()), before)
        ? before
        : null,
    ),
    feedToken(locals.user.id),
  ]);
  return {
    ...found,
    feed:
      token == null
        ? null
        : `${url.origin}/watchlist.atom?token=${token.token}`,
  };
};

export const actions: Actions = {
  unwatch: async ({ locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to change your watchlist." });
    }
    const form = await request.formData();
    const kind = field(form, "kind");
    const id = field(form, "id");
    if (!v.is(v.picklist(WATCH_KINDS), kind) || id == null || !v.is(Id, id)) {
      return fail(400, { message: "That isn't on your watchlist." });
    }
    await setWatch(locals.user.id, kind, id, false);
    return null;
  },
  rotate: async ({ locals }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to change your feed link." });
    }
    await feedToken(locals.user.id, true);
    return { rotated: true };
  },
};
