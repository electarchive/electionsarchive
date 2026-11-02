import {
  filtersFrom,
  ProposalFilters,
  proposalList,
} from "#lib/server/feeds.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url, setHeaders }) => {
  setHeaders({ "cache-control": "no-store" });
  const query = filtersFrom(ProposalFilters, url.searchParams);
  return { query, ...(await proposalList(query)) };
};
