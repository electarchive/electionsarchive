import { party } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) =>
  party(params.path, url.searchParams.get("in"));
