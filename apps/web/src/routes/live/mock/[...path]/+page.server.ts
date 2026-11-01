import { liveBallot } from "#lib/server/live-ballot.js";
import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, setHeaders }) => {
  setHeaders({ "x-robots-tag": "noindex, nofollow" });
  const [found, ballot] = await Promise.all([
    livePage(params.path, null),
    liveBallot(params.path),
  ]);
  return { ...found, ballot };
};
