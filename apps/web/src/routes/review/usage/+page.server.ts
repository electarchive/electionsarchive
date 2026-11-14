import { isAdmin } from "#lib/server/roles.js";
import { usageReport } from "#lib/server/usage.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  return (await isAdmin(locals.user?.id))
    ? { allowed: true, report: await usageReport() }
    : { allowed: false, report: null };
};
