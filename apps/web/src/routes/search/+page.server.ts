import { results } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ url }) =>
  results(url.searchParams);
