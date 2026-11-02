import { fail, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { Id } from "#lib/server/discussion-actions.js";
import { inbox, markRead, SHOWS } from "#lib/server/notifications.js";
import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  if (locals.user == null) {
    redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
  }
  const asked = url.searchParams.get("show");
  const show = v.is(v.picklist(SHOWS), asked) ? asked : "all";
  const before = url.searchParams.get("before");
  return {
    show,
    ...(await inbox(
      locals.user.id,
      show,
      before != null && v.is(v.pipe(v.string(), v.isoTimestamp()), before)
        ? before
        : null,
    )),
  };
};

export const actions: Actions = {
  read: async ({ locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to read your notifications." });
    }
    const ids = (await request.formData()).getAll("id");
    if (!v.is(v.pipe(v.array(Id), v.maxLength(100)), ids)) {
      return fail(400, { message: "Those aren't your notifications." });
    }
    await markRead(locals.user.id, ids);
    return { read: ids.length };
  },
  all: async ({ locals }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to read your notifications." });
    }
    await markRead(locals.user.id, null);
    return { marked: true };
  },
};
