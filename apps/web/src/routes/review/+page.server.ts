import { queue, roles } from "#lib/server/contrib.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const allowed = await roles(locals.user?.id);
  return allowed.admin || allowed.reviewer
    ? { allowed: true, proposals: await queue() }
    : { allowed: false, proposals: [] };
};
