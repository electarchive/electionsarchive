import { filtersFrom, recent, RecentFilters } from "#lib/server/feeds.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  return recent(filtersFrom(RecentFilters, url.searchParams));
};
