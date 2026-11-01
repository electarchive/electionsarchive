import { liveBallot } from "#lib/server/live-ballot.js";
import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const found = await livePage(params.path, url.searchParams.get("at"));
  return { ...found, ballot: await liveBallot(found) };
};
