import type { LiveElection, LiveEstimate } from "@ea/analytics/live";
import { part } from "svelte-islands/server";
import type { Control, Trail } from "#lib/live/nation.js";
import type { LivePage } from "#lib/server/live-page.js";
import { holder } from "#lib/live/ballot.js";
import { pulse, sides } from "#lib/live/nation.js";
import { liveBallot } from "#lib/server/live-ballot.js";
import { trend } from "#lib/server/live-estimates.js";
import { livePage } from "#lib/server/live-page.js";
import { liveParts } from "#lib/server/live-parts.js";
import type { PageServerLoad } from "./$types";

const trails = new WeakMap<
  LiveElection,
  { control: Control | null; trail: Trail }
>();

const slims = new WeakMap<
  LiveElection,
  Map<
    string,
    {
      from: Record<string, LiveEstimate>;
      election: LiveElection;
      estimates: Record<string, LiveEstimate>;
    }
  >
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
  const { election, estimates, control, geography, ...rest } = page;
  const isSingle = page.isRace && page.place == null;
  const night = isSingle ? slim(page) : page;
  const focus =
    isSingle && page.shown.forecasts
      ? (election.races.find((race) => race.slug === page.focus) ?? null)
      : null;
  const contest =
    page.focus == null
      ? null
      : (state?.contests.find((entry) => entry.contest.slug === page.focus)
          ?.contest ?? null);
  return {
    ...rest,
    trail: page.nation == null ? null : trail(election, control),
    board: liveParts(night.election, night.estimates, control).board,
    geography:
      geography == null
        ? null
        : part("live.map", geography, { fetch: "preload" }),
    ballot:
      ballot == null ? null : part("live.ballot", ballot, { fetch: "preload" }),
    points:
      focus == null
        ? null
        : part("live.trend", await trend(focus), { fetch: "preload" }),
    seat:
      contest == null || contest.isMeasure
        ? null
        : {
            held: holder(state?.parties ?? {}, contest.held),
            families: state?.families ?? {},
          },
  };
};

function trail(election: LiveElection, control: Control | null) {
  let found = trails.get(election);
  if (found?.control !== control) {
    const pair = sides(control);
    found = {
      control,
      trail:
        pair == null
          ? []
          : pulse(
              election.races.filter((race) => race.office === "house"),
              control,
              pair,
            ),
    };
    trails.set(election, found);
  }
  return found.trail;
}

function slim(page: LivePage) {
  const key = page.focus ?? "";
  let held = slims.get(page.election);
  if (held == null) {
    held = new Map();
    slims.set(page.election, held);
  }
  let found = held.get(key);
  if (found?.from !== page.estimates) {
    found = { from: page.estimates, ...slimmed(page) };
    held.set(key, found);
  }
  return found;
}

function slimmed(page: LivePage) {
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
