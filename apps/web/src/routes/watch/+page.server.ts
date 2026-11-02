import { fail, isHttpError, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { field, Id } from "#lib/server/discussion-actions.js";
import { setWatch, WATCH_KINDS } from "#lib/server/feeds.js";
import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = () => {
  redirect(307, "/watchlist");
};

const local = (value: string | null) =>
  value != null && value.startsWith("/") && !value.startsWith("//")
    ? value
    : "/watchlist";

export const actions: Actions = {
  default: async ({ locals, request }) => {
    const form = await request.formData();
    const back = local(field(form, "back"));
    if (locals.user == null) {
      redirect(303, `/sign-in?next=${encodeURIComponent(back)}`);
    }
    const kind = field(form, "kind");
    const id = field(form, "id");
    if (!v.is(v.picklist(WATCH_KINDS), kind) || id == null || !v.is(Id, id)) {
      return fail(400, { message: "That isn't something you can watch." });
    }
    try {
      await setWatch(locals.user.id, kind, id, field(form, "on") === "true");
    } catch (error_) {
      if (isHttpError(error_)) {
        return fail(error_.status, { message: error_.body.message });
      }
      throw error_;
    }
    redirect(303, back);
  },
};
