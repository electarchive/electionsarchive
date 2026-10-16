import { archive } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) =>
  archive(params.path, url.searchParams);
