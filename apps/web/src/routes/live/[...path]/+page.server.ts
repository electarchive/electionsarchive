import type { LiveElection } from "@ea/analytics/live";
import type { Control, Trail } from "#lib/live/nation.js";
import type { LivePage } from "#lib/server/live-page.js";
import { holder } from "#lib/live/ballot.js";
import { pulse, sides } from "#lib/live/nation.js";
import { liveBallot } from "#lib/server/live-ballot.js";
import { livePage } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

const hubs = new WeakMap<
  LiveElection,
  { control: Control | null; trail: Trail; election: LiveElection }
>();

export const load: PageServerLoad = async ({ params, url }) => {
  const segments = params.path.split("/");
  const [page, ballot, state] = await Promise.all([
    livePage(params.path, url.searchParams.get("at")),
    liveBallot(params.path),
    segments.length === 4
      ? liveBallot(segments.slice(0, 3).join("/"))
      : Promise.resolve(null),
  ]);
  const found =
    page.nation != null
      ? hub(page)
      : {
          ...(page.isRace && page.place == null ? slim(page) : page),
          trail: null,
        };
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
        : {
            held: holder(state?.parties ?? {}, contest.held),
            families: state?.families ?? {},
          },
  };
};

function hub(page: LivePage) {
  const { control } = page;
  const kept = hubs.get(page.election);
  if (kept?.control === control) {
    return { ...page, election: kept.election, trail: kept.trail };
  }
  const pair = sides(control);
  const { races } = page.election;
  const made = {
    control,
    trail:
      pair == null
        ? []
        : pulse(
            races.filter((race) => race.office === "house"),
            control,
            pair,
          ),
    election: {
      ...page.election,
      races: races.map((race) => ({ ...race, frames: race.frames.slice(-1) })),
    },
  };
  hubs.set(page.election, made);
  return { ...page, election: made.election, trail: made.trail };
}

function slim(page: LivePage) {
  const { races } = page.election;
  const focus = races.find((race) => race.slug === page.focus) ?? null;
  const anchor =
    focus != null && focus.office !== "house"
      ? focus
      : (races.find((race) => race.office === "senate") ??
        races.find((race) => race.office === "governor") ??
        null);
  const full = new Set([focus?.id, anchor?.id]);
  const estimates = { ...page.estimates };
  for (const [id, estimate] of Object.entries(estimates)) {
    if (!full.has(id)) {
      estimates[id] = { ...estimate, units: [] };
    }
  }
  return {
    ...page,
    election: {
      ...page.election,
      races: races.map((race) =>
        full.has(race.id)
          ? race
          : {
              ...race,
              units: [],
              counts: [],
              baseline: [],
              frames: race.frames.slice(-1),
            },
      ),
    },
    estimates,
  };
}
