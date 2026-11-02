import { fail, redirect } from "@sveltejs/kit";
import * as v from "valibot";
import { CADENCES, saveSettings, settings } from "#lib/server/notifications.js";
import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  if (locals.user == null) {
    redirect(303, `/sign-in?next=${encodeURIComponent(url.pathname)}`);
  }
  const found = await settings(locals.user.id);
  return {
    direct: found.direct_email,
    digest: found.digest,
    verified: locals.user.emailVerified,
  };
};

export const actions: Actions = {
  default: async ({ locals, request }) => {
    if (locals.user == null) {
      return fail(401, { message: "Sign in to change your email settings." });
    }
    const form = await request.formData();
    const digest = form.get("digest");
    if (!v.is(v.picklist(CADENCES), digest)) {
      return fail(400, { message: "Choose how often to get the digest." });
    }
    await saveSettings(locals.user.id, {
      direct: form.get("direct") === "yes",
      digest,
    });
    return { saved: true };
  },
};
