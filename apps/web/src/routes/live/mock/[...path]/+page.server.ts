import { liveBallot } from "#lib/server/live-ballot.js";
import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, setHeaders }) => {
  setHeaders({ "x-robots-tag": "noindex, nofollow" });
  const found = await livePage(params.path, null);
  return { ...found, ballot: await liveBallot(found) };
};
