import { site } from "#lib/server/site.js";
import type { LayoutServerLoad } from "./$types";

export const load: LayoutServerLoad = async ({ locals }) => ({
  user:
    locals.user == null
      ? null
      : { name: locals.user.name, email: locals.user.email },
  site: await site(),
});
