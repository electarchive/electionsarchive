import { fail, redirect } from "@sveltejs/kit";
import { isAPIError } from "better-auth/api";
import { auth } from "#lib/server/auth.js";
import type { Actions, PageServerLoad } from "./$types";

const target = (url: URL) => {
  const next = url.searchParams.get("next") ?? "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
};

export const load: PageServerLoad = ({ locals, url }) => {
  if (locals.user != null) {
    redirect(303, target(url));
  }
  return { next: target(url) };
};

export const actions: Actions = {
  default: async ({ request, url }) => {
    const form = await request.formData();
    const name = String(form.get("name") ?? "");
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");
    try {
      await auth.api.signUpEmail({
        body: { name, email, password },
        headers: request.headers,
      });
    } catch (error) {
      if (isAPIError(error)) {
        return fail(400, { name, email, message: error.message });
      }
      throw error;
    }
    redirect(303, target(url));
  },
};
