import { holder } from "#lib/live/ballot.js";
import { liveBallot } from "#lib/server/live-ballot.js";
import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = async ({ params, url }) => {
  const segments = params.path.split("/");
  const [found, ballot, state] = await Promise.all([
    livePage(params.path, url.searchParams.get("at")),
    liveBallot(params.path),
    segments.length === 4
      ? liveBallot(segments.slice(0, 3).join("/"))
      : Promise.resolve(null),
  ]);
  const contest =
    found.focus == null
      ? null
      : (state?.contests.find((entry) => entry.contest.slug === found.focus)
          ?.contest ?? null);
  return {
    ...found,
    ballot,
    seat:
      contest == null || contest.isMeasure
        ? null
        : { held: holder(state?.parties ?? {}, contest.held) },
  };
};
