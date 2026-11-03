import { roles } from "#lib/server/contrib.js";
import { usageReport } from "#lib/server/usage.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const allowed = await roles(locals.user?.id);
  return allowed.admin
    ? { allowed: true, admin: true, report: await usageReport() }
    : { allowed: false, admin: false, report: null };
};
