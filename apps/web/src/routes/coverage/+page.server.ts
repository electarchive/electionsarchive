import { coverageOverview } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = ({ url }) =>
  coverageOverview(url.searchParams);
