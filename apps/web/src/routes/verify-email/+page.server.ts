import { fail, redirect } from "@sveltejs/kit";
import { allowAccountMail } from "#lib/server/account-mail.js";
import { auth } from "#lib/server/auth.js";
import type { Actions, PageServerLoad } from "./$types";

const target = (url: URL) => {
  const next = url.searchParams.get("next") ?? "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
};

export const load: PageServerLoad = ({ locals, url }) => {
  if (locals.user == null) {
    redirect(
      303,
      `/sign-in?next=${encodeURIComponent(url.pathname + url.search)}`,
    );
  }
  return {
    email: locals.user.email,
    verified: locals.user.emailVerified,
    next: target(url),
  };
};

export const actions: Actions = {
  default: async ({ locals, request, url }) => {
    if (locals.user == null) {
      redirect(303, "/sign-in?next=%2Fverify-email");
    }
    if (locals.user.emailVerified) {
      return { sent: false };
    }
    if (!(await allowAccountMail("verify", locals.user.id))) {
      return fail(429, { wait: true });
    }
    await auth.api.sendVerificationEmail({
      body: { email: locals.user.email, callbackURL: target(url) },
      headers: request.headers,
    });
    return { sent: true };
  },
};
