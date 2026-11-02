import { accounts } from "#lib/server/accounts.js";
import { roles } from "#lib/server/contrib.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ locals, setHeaders, url }) => {
  setHeaders({ "cache-control": "no-store" });
  const allowed = await roles(locals.user?.id);
  const q = url.searchParams.get("q")?.trim().slice(0, 100) ?? "";
  return allowed.admin || allowed.reviewer
    ? { allowed: true, q, accounts: await accounts(q === "" ? null : q) }
    : { allowed: false, q, accounts: [] };
};
