import { liveBallot } from "#lib/server/live-ballot.js";
import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const [found, ballot] = await Promise.all([
    livePage(params.path, url.searchParams.get("at")),
    liveBallot(params.path),
  ]);
  return { ...found, ballot };
};
