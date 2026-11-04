import { error, json } from "@sveltejs/kit";
import { trend } from "#lib/server/live-estimates.js";
import { night } from "#lib/server/live-page.js";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async ({ params }) => {
  const { election, shown } = await night(params.path);
  const race = election.races.find((entry) => entry.slug === params.path);
  if (race == null || !shown.forecasts) {
    error(404);
  }
  return json(await trend(race));
};
