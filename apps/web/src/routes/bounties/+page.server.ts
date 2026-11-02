import { filters, list } from "#lib/server/bounties.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  return list(filters(url.searchParams));
};
