import { fail } from "@sveltejs/kit";
import { isAPIError } from "better-auth/api";
import { auth } from "#lib/server/auth.js";
import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = ({ url }) => ({
  token: url.searchParams.get("token"),
});

export const actions: Actions = {
  default: async ({ request }) => {
    const form = await request.formData();
    const token = String(form.get("token") ?? "");
    const newPassword = String(form.get("password") ?? "");
    try {
      await auth.api.resetPassword({
        body: { token, newPassword },
        headers: request.headers,
      });
    } catch (error) {
      if (isAPIError(error)) {
        const invalid =
          error.body?.code === "INVALID_TOKEN" ||
          error.body?.code === "USER_NOT_FOUND";
        return fail(400, {
          invalid,
          message: invalid ? null : error.message,
          done: false,
        });
      }
      throw error;
    }
    return { invalid: false, message: null, done: true };
  },
};
