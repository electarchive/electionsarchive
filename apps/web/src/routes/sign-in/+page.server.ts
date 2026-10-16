import {
  GITHUB_CLIENT_ID,
  GITHUB_CLIENT_SECRET,
  GOOGLE_CLIENT_ID,
  GOOGLE_CLIENT_SECRET,
} from "$app/env/private";
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
  return {
    next: target(url),
    providers: [
      ...(GITHUB_CLIENT_ID != null && GITHUB_CLIENT_SECRET != null
        ? (["github"] as const)
        : []),
      ...(GOOGLE_CLIENT_ID != null && GOOGLE_CLIENT_SECRET != null
        ? (["google"] as const)
        : []),
    ],
  };
};

export const actions: Actions = {
  default: async ({ request, url }) => {
    const form = await request.formData();
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");
    try {
      await auth.api.signInEmail({
        body: { email, password },
        headers: request.headers,
      });
    } catch (error) {
      if (isAPIError(error)) {
        return fail(400, { email, message: error.message });
      }
      throw error;
    }
    redirect(303, target(url));
  },
};
