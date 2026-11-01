import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, setHeaders }) => {
  setHeaders({ "x-robots-tag": "noindex, nofollow" });
  return livePage(params.path, null);
};
