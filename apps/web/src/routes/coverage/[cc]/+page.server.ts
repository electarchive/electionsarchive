import { coverage } from "#lib/server/pages.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = ({ params, url }) =>
  coverage(params.cc, url.searchParams);
