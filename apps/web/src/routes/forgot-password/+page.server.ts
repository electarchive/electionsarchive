import { fail } from "@sveltejs/kit";
import { isAPIError } from "better-auth/api";
import { allowAccountMail } from "#lib/server/account-mail.js";
import { auth } from "#lib/server/auth.js";
import type { Actions } from "./$types";

export const actions: Actions = {
  default: async ({ request }) => {
    const form = await request.formData();
    const email = String(form.get("email") ?? "").trim();
    if (await allowAccountMail("reset", email)) {
      try {
        await auth.api.requestPasswordReset({
          body: { email },
          headers: request.headers,
        });
      } catch (error) {
        if (isAPIError(error)) {
          return fail(400, { email, message: error.message, sent: false });
        }
        throw error;
      }
    }
    return { email, message: null, sent: true };
  },
};
