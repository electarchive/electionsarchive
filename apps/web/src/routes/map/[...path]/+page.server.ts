import { atlasPage } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) =>
  atlasPage(params.path, url.searchParams);
