import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) =>
  livePage(params.path, url.searchParams.get("at"));
